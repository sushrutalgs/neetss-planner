"""
Post-mock debrief — Claude Opus.

Hit once after every mock exam the user finishes (manual trigger from the
mocks tab, or auto on the user-mock-history webhook). Opus is justified
here because:
  • The input is large (180-300 questions, full attempt log, distractor data).
  • The output drives a multi-week course correction, so accuracy beats cost.
  • It's <1 call/day per user, so latency/spend isn't a concern.

Output:
    {
      "rank_estimate":      "1450th percentile-equivalent",
      "score":              {raw, attempted, accuracy_pct, time_min},
      "topic_breakdown":    [{topic, attempted, correct, accuracy, severity}],
      "five_things":        ["...", ...],   # the 5 highest-impact takeaways
      "next_72_hours":      "markdown",      # specific drills
      "long_term_pattern":  "markdown",      # what this mock says about prep
      "raw_md":             "full markdown"
    }
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude, OPUS, ClaudeError

logger = logging.getLogger("planner.ai.mock_debrief")


def _topic_breakdown(attempts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bins: Dict[str, Dict[str, int]] = {}
    for a in attempts or []:
        tid = a.get("topic_id") or "_unknown"
        if tid not in bins:
            bins[tid] = {
                "topic_id": tid,
                "topic_name": a.get("topic_name", tid),
                "attempted": 0,
                "correct": 0,
                "time_seconds": 0.0,
            }
        bins[tid]["attempted"] += 1
        if a.get("correct"):
            bins[tid]["correct"] += 1
        bins[tid]["time_seconds"] += float(a.get("time_seconds") or 0)
    out = []
    for row in bins.values():
        att = row["attempted"]
        acc = row["correct"] / att if att else 0
        sev = "high" if acc < 0.5 else ("medium" if acc < 0.7 else "low")
        out.append({
            **row,
            "accuracy_pct": round(100 * acc, 1),
            "avg_time_seconds": round(row["time_seconds"] / max(1, att), 1),
            "severity": sev,
        })
    out.sort(key=lambda r: r["accuracy_pct"])
    return out


def debrief(
    mock_meta: Dict[str, Any],
    attempts: List[Dict[str, Any]],
    user_signal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run the Opus debrief on a finished mock.

    `mock_meta` is the mock header: {mock_id, mock_name, total_q, time_limit_min, ...}
    `attempts` is per-question rows from the LMS user-mock-history endpoint.
    `user_signal` provides the rolling planner context.
    """
    if not attempts:
        return {
            "raw_md": "No attempts recorded for this mock.",
            "score": {"raw": 0, "attempted": 0, "accuracy_pct": 0, "time_min": 0},
            "topic_breakdown": [],
        }

    raw_score = sum(1 for a in attempts if a.get("correct"))
    attempted = sum(1 for a in attempts if a.get("user_answer") not in (None, "", -1))
    total_time_min = sum(float(a.get("time_seconds") or 0) for a in attempts) / 60.0
    breakdown = _topic_breakdown(attempts)

    # Build a compact payload for Opus — full per-question rows would blow
    # the context, so we summarise + send the worst 30 question stems.
    worst = sorted(
        [a for a in attempts if not a.get("correct")],
        key=lambda a: -(a.get("difficulty") or 0),
    )[:30]
    payload = {
        "mock": {
            "id": str(mock_meta.get("mock_id", "")),
            "name": mock_meta.get("mock_name") or mock_meta.get("name", "Mock"),
            "total_questions": mock_meta.get("total_q") or len(attempts),
            "time_limit_min": mock_meta.get("time_limit_min"),
            "raw_score": raw_score,
            "attempted": attempted,
            "accuracy_pct": round(100 * raw_score / max(1, attempted), 1),
            "time_used_min": round(total_time_min, 1),
        },
        "topic_breakdown": breakdown[:25],
        "worst_questions": [
            {
                "qid": str(a.get("content_id", ""))[:32],
                "topic": a.get("topic_name", ""),
                "stem": (a.get("question_text") or "")[:240],
                "user_answer": a.get("user_answer"),
                "correct_answer": a.get("correct_option") or a.get("correct_answer"),
                "explanation": (a.get("explanation") or "")[:240],
                "time_seconds": a.get("time_seconds"),
                "difficulty": a.get("difficulty"),
            }
            for a in worst
        ],
        "user_context": {
            "streak": (user_signal or {}).get("computed", {}).get("streak_current", 0),
            "avg_min_14d": (user_signal or {}).get("computed", {}).get("avg_daily_minutes_14d", 0),
            "weakest_topics_pre_mock": [
                w.get("topic_name") for w in (user_signal or {}).get("computed", {}).get("weakest_topics", [])[:5]
            ],
        },
    }

    system = (
        "You are Cortex, a NEET SS surgical exam coach. The resident just finished a "
        "full-length mock. Your job is to give them the *highest-impact* debrief — "
        "what the mock says about their preparation, what to fix in the next 72 "
        "hours, and what long-term pattern this confirms or breaks. Be direct, "
        "clinical, and specific. No fluff. Reference real numbers from the payload."
    )
    prompt = (
        f"Mock data:\n\n{json.dumps(payload, indent=2)}\n\n"
        "Return JSON with these exact fields:\n"
        "  rank_estimate (string — short percentile-equivalent guess),\n"
        "  five_things (array of exactly 5 short strings — the 5 highest-impact takeaways),\n"
        "  next_72_hours (markdown — concrete drills, name specific topics),\n"
        "  long_term_pattern (markdown — 2 paragraphs),\n"
        "  raw_md (full markdown debrief, ~600 words, with H3 sections).\n"
        "Only JSON, no prose, no fences."
    )

    try:
        result = call_claude(
            prompt=prompt,
            system=system,
            model=OPUS,
            max_tokens=4500,
            temperature=0.5,
        )
        text = (result.get("text") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        parsed = json.loads(text)
    except (ClaudeError, ValueError, json.JSONDecodeError) as e:
        logger.warning("[mock_debrief] Opus failed: %s", e)
        parsed = {
            "rank_estimate": "n/a",
            "five_things": [
                "Mock complete — debrief generation hit a snag.",
                "Top weakness: " + (breakdown[0]["topic_name"] if breakdown else "n/a"),
                "Time/question average: "
                + str(round(total_time_min * 60 / max(1, len(attempts)), 1))
                + "s",
                "Accuracy: " + str(round(100 * raw_score / max(1, attempted), 1)) + "%",
                "Try regenerating the debrief in a moment.",
            ],
            "next_72_hours": "Focus on the lowest-accuracy topic from the breakdown table.",
            "long_term_pattern": "n/a",
            "raw_md": "Debrief generation failed; see fields above for the deterministic summary.",
        }

    return {
        "score": payload["mock"],
        "topic_breakdown": breakdown,
        "rank_estimate": parsed.get("rank_estimate"),
        "five_things": parsed.get("five_things", []),
        "next_72_hours": parsed.get("next_72_hours", ""),
        "long_term_pattern": parsed.get("long_term_pattern", ""),
        "raw_md": parsed.get("raw_md", ""),
        "generated_at": datetime.utcnow().isoformat(),
        "model": OPUS,
    }
