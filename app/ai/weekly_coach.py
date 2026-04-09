"""
Weekly coach — Sonnet-powered review of the past 7 days that tells the user
what to fix, what worked, and which topics to hammer next.

Called by `GET /api/ai-coach/v2/weekly-review` (defined in routers/ai_coach.py)
once per week per user. The legacy rule-based `/ai-coach/weekly-review`
endpoint stays in place for backwards compatibility with older clients.

Contract: takes a context dict assembled by the router and returns a
structured review JSON. Failure returns None — the caller falls back to
the legacy template so the user is never stranded.
"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude_json, ClaudeError, SONNET

logger = logging.getLogger("planner.ai.weekly_coach")

SYSTEM_PROMPT = """You are Cortex, an expert NEET SS surgery study coach.

You are producing a weekly review for a surgical aspirant based on the last
7 days of their study data and their overall trajectory. You are blunt,
specific, and numbers-driven. You never generate empty encouragement — every
sentence is either a fact, a prescription, or a measurable target.

Style:
  - Second-person ("you", "your")
  - Always cite the actual numbers you're referring to (hours, accuracy, %)
  - Prescribe concrete next actions (topic names, question counts, day counts)
  - No filler phrases like "keep up the great work" — replace with a
    specific observation + next step.

OUTPUT FORMAT: Respond with ONLY valid JSON matching this schema:
{
  "headline":          "<one-line summary, no more than 90 chars>",
  "what_worked":       ["<bullet>", "<bullet>"],
  "what_to_fix":       ["<bullet>", "<bullet>", "<bullet>"],
  "next_week_targets": {
    "study_days":     <int 1-7>,
    "mcqs":           <int>,
    "topics_to_hit":  ["<topic name>", ...],
    "accuracy_goal":  <int 0-100>
  },
  "rendered_md":       "<full markdown brief, 250-400 words, combining everything>"
}

The `rendered_md` field is the canonical display format.
"""


def generate_weekly_review(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Claude-powered weekly review. `ctx` should be the same dict produced by
    `_build_coach_context` in ai_coach.py, enriched with a few extra fields:

        {
          "user_name":        str,
          "exam_date":        "YYYY-MM-DD" or None,
          "days_remaining":   int or None,
          "streak":           int,
          "recent_mcqs":      [{ topic, attempted, correct, accuracy }],
          "recent_study_days":[{ date, hours }],
          "all_time_accuracy":[{ topic, accuracy }],
          "weak_topics":      [str],
          "strong_topics":    [str],
          "untouched_topics": [str],
          "current_phase":    str or None,
          "mastery_snapshot": { topic_id: {mastery, accuracy, days_idle} }
        }

    Returns the parsed JSON dict on success or None on failure.
    """
    if not ctx:
        return None

    # Keep the prompt bounded — Claude doesn't need thousands of topic rows.
    ctx_slim: Dict[str, Any] = {
        "user_name": ctx.get("user_name") or "Student",
        "exam_date": ctx.get("exam_date"),
        "days_remaining": ctx.get("days_remaining"),
        "streak": ctx.get("streak"),
        "recent_study_days": ctx.get("recent_study_days") or [],
        "recent_mcqs": (ctx.get("recent_mcqs") or [])[:15],
        "weak_topics": (ctx.get("weak_topics") or [])[:8],
        "strong_topics": (ctx.get("strong_topics") or [])[:5],
        "untouched_topics": (ctx.get("untouched_topics") or [])[:5],
        "current_phase": ctx.get("current_phase"),
    }

    study_days = len(ctx_slim["recent_study_days"])
    total_hours = sum(d.get("hours", 0) for d in ctx_slim["recent_study_days"])
    weekly_mcqs = sum(r.get("attempted", 0) for r in ctx_slim["recent_mcqs"])
    weekly_correct = sum(r.get("correct", 0) for r in ctx_slim["recent_mcqs"])
    weekly_accuracy = round((weekly_correct / weekly_mcqs) * 100, 1) if weekly_mcqs else 0.0

    ctx_slim["week_stats"] = {
        "study_days_out_of_7": study_days,
        "total_hours": round(total_hours, 1),
        "mcqs": weekly_mcqs,
        "accuracy_pct": weekly_accuracy,
    }

    prompt = (
        "Here is this student's last 7 days of study data plus their overall "
        "trajectory. Produce the weekly review JSON per the system prompt.\n\n"
        "CONTEXT:\n```json\n"
        + json.dumps(ctx_slim, indent=2)
        + "\n```\n\n"
        "Prescription rules:\n"
        "- If study_days_out_of_7 < 4, the #1 `what_to_fix` item MUST be "
        "  about consistency before anything else.\n"
        "- `next_week_targets.mcqs` should be ≥ this week's count unless the "
        "  student is clearly overtraining (>7h/day average).\n"
        "- `topics_to_hit` = the 3 weakest topics, by name, verbatim from the "
        "  `weak_topics` list. If that list is empty, use `untouched_topics`.\n"
        "- If `days_remaining` is not null and < 30, override everything: "
        "  push the student into Final-phase mode (mocks + revision).\n"
        "- `accuracy_goal` should be the current weekly accuracy + 5, "
        "  clamped to [60, 90]."
    )

    try:
        res = call_claude_json(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            model=SONNET,
            max_tokens=4000,
            temperature=0.6,
        )
    except ClaudeError as e:
        logger.warning("[weekly_coach] claude call failed: %s", e)
        return None

    data = res.get("json")
    if not isinstance(data, dict):
        logger.warning("[weekly_coach] non-JSON output: %r", (res.get("text") or "")[:200])
        return None
    if not (data.get("rendered_md") or "").strip():
        logger.warning("[weekly_coach] empty rendered_md — discarding")
        return None

    data["_tokens"] = {
        "input": res.get("input_tokens"),
        "output": res.get("output_tokens"),
        "total": res.get("tokens"),
        "model": res.get("model"),
    }
    return data
