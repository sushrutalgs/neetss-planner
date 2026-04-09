"""
Plan-rationale generator — Claude Sonnet narrates the freshly built schedule
so the user knows *why* their plan looks the way it does.

This is the first true AI surface in the planner. It runs once at
`/plans/generate` time, after the rule-based scheduler has produced the day
list, and returns a markdown brief that the Flutter/web client renders above
the Today screen.

Design notes:
  - Sonnet, not Opus — this is bulk generation, not deep reasoning.
  - JSON output so the client can render sections deterministically even if
    the prose is slightly different each time.
  - Fast-fail: any error returns `None` and the caller falls back to the
    rule-based rationale baked into each block. We NEVER block plan
    generation on Claude availability.
  - Budget: ~6K output tokens, ~3K input tokens. Keeps the call under 3s
    typical latency.
"""
from __future__ import annotations
import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude_json, ClaudeError, SONNET

logger = logging.getLogger("planner.ai.rationale")

SYSTEM_PROMPT = """You are Cortex, an expert NEET SS surgery study coach.

You produce a concise, personalized plan rationale for a surgical aspirant's
newly generated study schedule. You explain WHY the plan is structured the
way it is, what the first week focuses on, which weak areas need attention,
and what the student should expect in the final phase before the exam.

You are NOT generating a new plan — only narrating the one that's already
been built by the scheduler. Stay grounded in the data you're given.

Tone: direct, confident, encouraging but not saccharine. Think senior
registrar giving a juniormiles-based orientation, not a chatbot.

OUTPUT FORMAT: Respond with ONLY valid JSON matching this schema:
{
  "summary_md":        "<one-paragraph headline, markdown, 2-3 sentences>",
  "first_week_focus":  ["<bullet 1>", "<bullet 2>", "<bullet 3>"],
  "weak_area_plan":    "<short markdown on how weak topics will be attacked>",
  "phase_breakdown": [
    { "phase": "Foundation",    "days": <int>, "focus": "<one line>" },
    { "phase": "Consolidation", "days": <int>, "focus": "<one line>" },
    { "phase": "Revision",      "days": <int>, "focus": "<one line>" },
    { "phase": "Final",         "days": <int>, "focus": "<one line>" }
  ],
  "exam_day_tip":      "<one-line final-week advice>",
  "rendered_md":       "<full markdown brief, ~200-350 words, combining the above into a single document the UI can paste directly into a Markdown renderer>"
}

The `rendered_md` field is the canonical one the UI will display. The
structured fields above are available for clients that want to lay out
sections individually.
"""


def _phase_histogram(days: List[Dict[str, Any]]) -> Dict[str, int]:
    hist: Dict[str, int] = {}
    for d in days:
        phase = d.get("phase") or "Unknown"
        hist[phase] = hist.get(phase, 0) + 1
    return hist


def _first_week_summary(days: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for d in days[:7]:
        blocks = d.get("blocks") or []
        topic_names = []
        for b in blocks:
            tref = (b.get("topic_ref") or {}).get("name")
            if tref and tref not in topic_names:
                topic_names.append(tref)
        out.append({
            "day": d.get("day"),
            "phase": d.get("phase"),
            "time_budget_min": d.get("time_budget_min"),
            "block_kinds": [b.get("kind") for b in blocks],
            "topics": topic_names[:4],
        })
    return out


def _weak_topic_summary(mastery: Dict[str, Dict[str, Any]], topics_meta: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Top 5 weakest topics by (1 - mastery_score), only counting topics
    the user has actually touched. Cold-start users get an empty list
    and Claude is told "no mastery data yet".
    """
    scored = []
    for tid, m in mastery.items():
        score = m.get("mastery") or 0.0
        acc = m.get("accuracy") or 0.0
        idle = m.get("last_studied_days_ago", 999)
        # Heuristic: weakness = low mastery + recent activity
        scored.append({
            "topic_id": tid,
            "topic_name": topics_meta.get(tid, tid),
            "mastery": round(score, 2),
            "accuracy": round(acc, 2),
            "days_idle": idle,
        })
    scored.sort(key=lambda x: (x["mastery"], -x["accuracy"]))
    return scored[:5]


def generate_plan_rationale(
    days: List[Dict[str, Any]],
    exam_date: date,
    hours_per_day: float,
    mastery: Dict[str, Dict[str, Any]],
    bundle: Dict[str, Any],
    subscription_status: str,
) -> Optional[Dict[str, Any]]:
    """
    Call Sonnet to narrate the fresh plan. Returns the parsed JSON dict on
    success, or None on any failure (network, parse, empty response). The
    caller should treat None as "no rationale today" and render nothing.
    """
    if not days:
        return None

    phase_hist = _phase_histogram(days)
    week1 = _first_week_summary(days)

    # Build a topic id → name map from the bundle for the weak-topic table.
    topic_names: Dict[str, str] = {}
    for cat in bundle.get("categories", []) or []:
        for sub in cat.get("subcategories", []) or []:
            for t in sub.get("topics", []) or []:
                topic_names[str(t.get("_id"))] = t.get("name", "")

    weak = _weak_topic_summary(mastery, topic_names)
    total_days = len(days)
    rest_days = sum(1 for d in days if d.get("phase") == "Rest")
    study_days = total_days - rest_days

    payload = {
        "exam_date": exam_date.isoformat(),
        "total_days": total_days,
        "study_days": study_days,
        "rest_days": rest_days,
        "hours_per_day": hours_per_day,
        "subscription_status": subscription_status,
        "use_lms_content": bool(bundle.get("categories")),
        "phase_histogram": phase_hist,
        "first_week": week1,
        "weak_topics": weak,
        "topic_universe_size": len(topic_names),
    }

    prompt = (
        "Here is the freshly generated study plan snapshot for a NEET SS "
        "surgery aspirant. Produce the rationale JSON per the system prompt.\n\n"
        "PLAN SNAPSHOT:\n```json\n"
        + json.dumps(payload, indent=2)
        + "\n```\n\n"
        "Key instructions:\n"
        "- If `weak_topics` is empty, say \"cold start — the first week "
        "   intentionally casts a wide net so we can measure your baseline\".\n"
        "- If `use_lms_content` is false, mention that the plan currently "
        "  runs in chapter-reference mode and subscribing unlocks the full "
        "  content-aware scheduling.\n"
        "- If `subscription_status` is \"grace\", gently remind the user "
        "  that they're on the 3-day grace period after expiry.\n"
        "- The `rendered_md` field MUST be complete markdown ready to display."
    )

    try:
        res = call_claude_json(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            model=SONNET,
            max_tokens=6000,
            temperature=0.7,
        )
    except ClaudeError as e:
        logger.warning("[rationale] claude call failed: %s", e)
        return None

    data = res.get("json")
    if not isinstance(data, dict):
        logger.warning("[rationale] claude returned non-JSON: %r", (res.get("text") or "")[:200])
        return None

    # Minimal sanity: require rendered_md to be present and non-empty.
    rendered = data.get("rendered_md") or ""
    if not rendered.strip():
        logger.warning("[rationale] rendered_md missing — discarding")
        return None

    # Tack on token usage so callers can log spend.
    data["_tokens"] = {
        "input": res.get("input_tokens"),
        "output": res.get("output_tokens"),
        "total": res.get("tokens"),
        "model": res.get("model"),
    }
    return data
