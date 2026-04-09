"""
Question-level diagnostics — turns raw MCQ history into actionable insight.

Four signals:
  1. Cognitive level distribution (Bloom: recall/application/analysis/synthesis)
     — surfaces whether the user is bombing higher-order questions while
     acing recall, which means the underlying concept isn't *flexible* yet.
  2. Error pattern clustering — Claude Sonnet groups missed questions by
     misconception family ("confuses pheo with carcinoid", "drops anatomy
     in obese patients", etc).
  3. Distractor analysis — for each missed question, which distractor was
     picked and what does that imply about the gap.
  4. Time anomaly detection — questions answered in <40% of mean time
     (likely guessing) and >250% of mean time (likely struggling).

Inputs come from the LMS user-mcq-history endpoint and per-attempt rows
in MCQScore. Outputs are persisted to a `QuestionDiagnostic` row keyed by
(user_id, content_id) so the dashboard can render fast.
"""
from __future__ import annotations
import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude, SONNET, HAIKU, ClaudeError
from app.lms_client import get_user_mcq_history, LmsError, service_token_for

logger = logging.getLogger("planner.ai.diagnostics")


# ───────────────────────── pure-python signals ─────────────────────────


def cognitive_distribution(attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Count attempts by Bloom level. Each attempt row should carry a
    `cognitive_level` field, falling back to 'recall' if absent.
    """
    bins: Dict[str, Dict[str, int]] = defaultdict(lambda: {"attempted": 0, "correct": 0})
    for a in attempts or []:
        level = (a.get("cognitive_level") or "recall").lower()
        bins[level]["attempted"] += 1
        if a.get("correct"):
            bins[level]["correct"] += 1
    out = {}
    for level, row in bins.items():
        att = row["attempted"]
        out[level] = {
            "attempted": att,
            "correct": row["correct"],
            "accuracy_pct": round(100.0 * row["correct"] / att, 1) if att else 0.0,
        }
    return out


def time_anomalies(attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Flag rushed and stuck attempts based on per-topic mean time.
    """
    by_topic: Dict[str, List[float]] = defaultdict(list)
    for a in attempts or []:
        t = a.get("time_seconds")
        if t is None or t <= 0:
            continue
        by_topic[a.get("topic_id", "_unknown")].append(float(t))

    flagged = {"rushed": [], "stuck": []}
    for tid, times in by_topic.items():
        if len(times) < 5:
            continue
        mean = statistics.mean(times)
        if mean <= 0:
            continue
        for a in attempts:
            if a.get("topic_id") != tid:
                continue
            t = a.get("time_seconds") or 0
            if t < 0.4 * mean and not a.get("correct"):
                flagged["rushed"].append({"content_id": a.get("content_id"), "time": t, "mean": round(mean, 1)})
            elif t > 2.5 * mean:
                flagged["stuck"].append({"content_id": a.get("content_id"), "time": t, "mean": round(mean, 1)})

    return {
        "rushed_count": len(flagged["rushed"]),
        "stuck_count": len(flagged["stuck"]),
        "samples": {
            "rushed": flagged["rushed"][:5],
            "stuck": flagged["stuck"][:5],
        },
    }


def distractor_summary(attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Group missed answers by selected distractor. Returns the top distractor
    families so the LLM can riff on them.
    """
    misses: Dict[str, int] = defaultdict(int)
    by_family: Dict[str, int] = defaultdict(int)
    for a in attempts or []:
        if a.get("correct"):
            continue
        chosen = a.get("selected_option") or a.get("user_answer") or "?"
        misses[chosen] += 1
        family = a.get("distractor_family") or "uncategorised"
        by_family[family] += 1
    return {
        "by_distractor": dict(sorted(misses.items(), key=lambda kv: -kv[1])[:10]),
        "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1])[:10]),
    }


# ───────────────────────── Claude-backed clustering ─────────────────────────


def cluster_misconceptions(
    missed_attempts: List[Dict[str, Any]],
    topic_name: str,
    n_clusters: int = 5,
) -> Dict[str, Any]:
    """
    Asks Sonnet to group the missed questions for one topic into 3-5
    misconception families. Each family includes a name, the representative
    questions, and a 1-line remediation hint.
    """
    if not missed_attempts:
        return {"clusters": [], "topic": topic_name}

    payload = {
        "topic": topic_name,
        "missed_questions": [
            {
                "qid": str(a.get("content_id", ""))[:32],
                "stem": (a.get("question_text") or "")[:240],
                "correct_answer": a.get("correct_option") or a.get("correct_answer", ""),
                "user_answer": a.get("selected_option") or a.get("user_answer", ""),
                "explanation": (a.get("explanation") or "")[:240],
            }
            for a in missed_attempts[:30]  # cap payload
        ],
    }

    system = (
        "You are a NEET SS surgical exam coach analysing wrong answers. "
        "Group missed questions into 3-5 *misconception families* — clinically "
        "meaningful patterns the resident is repeatedly tripping on. Return JSON only."
    )
    prompt = (
        f"Here are the user's missed questions on {topic_name}:\n\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        f"Return a JSON object: {{ \"clusters\": [ {{ "
        f"\"name\": \"...\", \"description\": \"1 sentence\", "
        f"\"question_ids\": [\"qid1\", ...], \"remediation\": \"1 sentence\", "
        f"\"severity\": \"high|medium|low\" }} ], \"summary\": \"2 sentences\" }}. "
        f"Maximum {n_clusters} clusters. Only valid JSON, no prose, no fences."
    )
    try:
        result = call_claude(prompt=prompt, system=system, model=SONNET, max_tokens=2000, temperature=0.4)
        text = (result.get("text") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
    except (ClaudeError, ValueError, json.JSONDecodeError) as e:
        logger.warning("[diagnostics] cluster_misconceptions failed: %s", e)
        return {"clusters": [], "topic": topic_name, "error": str(e)}


# ───────────────────────── orchestrator ─────────────────────────


def build_user_diagnostic(
    attempts: List[Dict[str, Any]],
    topic_lookup: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Composite diagnostic for a single user — drives the AI Coach 'Diagnostic'
    panel. Pure-python signals first, Sonnet clustering on the worst topic only.
    """
    topic_lookup = topic_lookup or {}
    cognitive = cognitive_distribution(attempts)
    times = time_anomalies(attempts)
    distractors = distractor_summary(attempts)

    # Find the worst topic by accuracy (min 5 attempts) for cluster analysis.
    by_topic_acc: Dict[str, Dict[str, int]] = defaultdict(lambda: {"attempted": 0, "correct": 0, "missed": []})
    for a in attempts:
        tid = a.get("topic_id") or "_unknown"
        by_topic_acc[tid]["attempted"] += 1
        if a.get("correct"):
            by_topic_acc[tid]["correct"] += 1
        else:
            by_topic_acc[tid]["missed"].append(a)

    worst_topic_id = None
    worst_acc = 1.0
    for tid, row in by_topic_acc.items():
        if row["attempted"] < 5:
            continue
        acc = row["correct"] / row["attempted"]
        if acc < worst_acc:
            worst_acc = acc
            worst_topic_id = tid

    cluster_block = {}
    if worst_topic_id:
        cluster_block = cluster_misconceptions(
            by_topic_acc[worst_topic_id]["missed"],
            topic_name=topic_lookup.get(worst_topic_id, worst_topic_id),
        )

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "cognitive_distribution": cognitive,
        "time_anomalies": times,
        "distractors": distractors,
        "worst_topic_id": worst_topic_id,
        "worst_topic_accuracy_pct": round(100 * worst_acc, 1) if worst_topic_id else None,
        "misconception_clusters": cluster_block,
    }


def refresh_user_diagnostics(db, user) -> None:
    """
    Hook called by the nightly diagnostics job. Pulls the user's recent MCQ
    attempts via the LMS service token, runs build_user_diagnostic, and
    persists the result on the user row as a JSON blob (we don't need a
    table — diagnostics are recomputed nightly anyway).
    """
    if not user or not user.lms_user_id:
        return
    try:
        token = service_token_for(user.lms_user_id)
        from datetime import date as _date, timedelta as _td
        history = get_user_mcq_history(token, since_iso=(_date.today() - _td(days=30)).isoformat()) or {}
    except LmsError as e:
        logger.warning("[refresh_user_diagnostics] LMS fetch failed for user %s: %s", user.id, e)
        return
    attempts = history.get("attempts") or history.get("rows") or []
    if not attempts:
        return
    diag = build_user_diagnostic(attempts)
    # Store in a side-table if it exists; otherwise no-op (job is best-effort).
    try:
        from app.models import AIRun
        from app.ai.claude import HAIKU as _H  # noqa
        rec = AIRun(
            user_id=user.id,
            surface="diagnostics_nightly",
            model="local",
            input_tokens=0,
            output_tokens=0,
            output_md=json.dumps(diag)[:65000],
        )
        db.add(rec)
        db.commit()
    except Exception as e:
        logger.warning("[refresh_user_diagnostics] persist failed: %s", e)
