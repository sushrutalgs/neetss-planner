"""
AI plan shaper — Sonnet reads the unified mastery vector + bundle + time
budget and returns a structured blueprint the rule-based scheduler then
lays down onto calendar days.

This is the step that turns the planner from "rule engine with AI
narration" into "AI-driven adaptive plan".

Output contract (strict JSON):

    {
      "phase_windows": [
        {"phase": "Foundation",    "days": 18},
        {"phase": "Consolidation", "days": 24},
        {"phase": "Revision",      "days": 28},
        {"phase": "Final",         "days": 20}
      ],
      "ordered_topic_ids": ["...", "...", ...],
      "weak_blitzes": [
        {"topic_id": "...", "days": [12, 21, 35], "why": "..."}
      ],
      "diagnostic_week": [
        {"day_offset": 0, "topic_ids": ["..."], "n": 20},
        {"day_offset": 1, "topic_ids": ["..."], "n": 20}
      ],
      "strategy_md": "<short markdown paragraph the coach surfaces>"
    }

Failure modes (any of these → return None so build_schedule falls back to
rules): Claude error, non-JSON response, missing `phase_windows`,
`ordered_topic_ids` with no known topic ids.

The caller must guard `plan_shape` against None and treat None as
"use rule-based scheduling".
"""
from __future__ import annotations
import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude_json, ClaudeError, SONNET

logger = logging.getLogger("planner.ai.shaper")

# Feature flag: if PLAN_SHAPER_ENABLED is "0"/"false", callers should skip
# invoking the shaper entirely (the env check lives here so the call site
# stays tidy).
SHAPER_ENABLED = os.getenv("PLAN_SHAPER_ENABLED", "1").lower() not in ("0", "false", "no")

SYSTEM_PROMPT = """You are Cortex, a senior NEET SS surgery exam coach who designs
adaptive study blueprints for post-MBBS surgical aspirants.

You are given:
  - The student's unified mastery vector (per-topic accuracy, coverage,
    engagement, spaced-repetition state, cold-start flag).
  - The LMS library (topics with content counts of notes, videos, MCQs).
  - The plan window: start_date, end_date, hours_per_day, total days.
  - The subscription status.
  - The student's recent daily activity (14d average, streak).

You must output a strict JSON blueprint describing:
  1. Phase windows (Foundation → Consolidation → Revision → Final) whose
     day counts sum to `days_available`. Final phase is at least
     min(20, days_available // 4).
  2. `ordered_topic_ids`: every topic the student's LMS subscription
     exposes, sorted in the order they should FIRST appear in the plan.
     Weak + cold P1 topics go first; mature + strong topics go last.
     Use ONLY topic ids that appear in the input bundle.
  3. Weak-topic blitzes: up to 6 topics where the student has >=10 MCQ
     attempts and accuracy <60%. Each blitz specifies 2-3 day offsets
     (relative to plan start) when the topic gets a dedicated extra
     practice block. Spread blitzes across phases.
  4. Diagnostic week: if the student has <50 lifetime MCQ attempts, pick
     one P1 topic per day for the first 5 study days and set n=20. If the
     student is NOT a cold start, return an empty list.
  5. `strategy_md`: one paragraph (40-80 words, markdown) the UI surfaces
     above the Today screen. Directly addresses the student, references
     their real numbers, confident and specific.

OUTPUT FORMAT: Respond with ONLY a single valid JSON object matching the
schema above. No markdown fences, no prose.
"""


def _compact_vector_for_prompt(
    vector: Dict[str, Dict[str, Any]],
    bundle_topics: List[Dict[str, Any]],
    max_rows: int = 40,
) -> List[Dict[str, Any]]:
    """
    Compact the mastery vector down to the shape Sonnet needs. We include:
      - every evidenced topic (attempted or touched or has FSRS)
      - top N cold P1 topics by bundle order
    Total capped at max_rows to keep the prompt tight.
    """
    rows: List[Dict[str, Any]] = []
    for r in vector.values():
        drivers = r.get("drivers") or {}
        mcq_d = drivers.get("mcq") if isinstance(drivers.get("mcq"), dict) else {}
        content_d = drivers.get("content") if isinstance(drivers.get("content"), dict) else {}
        engagement_d = drivers.get("engagement") if isinstance(drivers.get("engagement"), dict) else {}
        rows.append({
            "topic_id": r.get("topic_id"),
            "topic_name": r.get("topic_name") or "",
            "mastery": round(float(r.get("mastery") or 0), 2),
            "gap": round(float(r.get("gap") or 0), 2),
            "attempted": int(mcq_d.get("attempted") or 0),
            "accuracy": round(float(mcq_d.get("accuracy") or 0), 1),
            "videos": int(content_d.get("videos_watched") or 0),
            "notes": int(content_d.get("notes_opened") or 0),
            "days_idle": engagement_d.get("last_touched_days_ago"),
            "cold": bool(drivers.get("cold")),
        })
    # Sort: evidenced first (by gap desc), then cold (by topic_name).
    rows.sort(
        key=lambda x: (
            0 if not x["cold"] else 1,
            -(x.get("gap") or 0),
            x["topic_name"] or "",
        )
    )
    return rows[:max_rows]


def _bundle_topic_catalog(bundle_topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One-line topic rows for the LMS library section of the prompt."""
    out = []
    for t in bundle_topics:
        counts = t.get("content_counts") or {}
        out.append({
            "topic_id": str(t.get("_id") or t.get("topic_id") or ""),
            "name": t.get("name") or t.get("topic_name") or "",
            "category": t.get("_category_name") or (t.get("category") or ""),
            "priority": t.get("priority_label") or "P2",
            "videos": int(counts.get("videos") or 0),
            "notes": int(counts.get("notes") or 0),
            "mcqs": int(counts.get("mcqs") or 0),
        })
    return out


def _validate_shape(
    shape: Any,
    days_available: int,
    known_topic_ids: set[str],
) -> Optional[Dict[str, Any]]:
    """Return the shape dict iff it passes sanity checks; else None."""
    if not isinstance(shape, dict):
        return None

    windows = shape.get("phase_windows")
    if not isinstance(windows, list) or not windows:
        return None
    # Normalise window days so they sum to days_available.
    total_window_days = sum(int(w.get("days") or 0) for w in windows)
    if total_window_days <= 0:
        return None
    if total_window_days != days_available:
        scale = days_available / total_window_days
        acc = 0
        for i, w in enumerate(windows):
            if i == len(windows) - 1:
                w["days"] = max(1, days_available - acc)
            else:
                w["days"] = max(1, int(round(int(w.get("days") or 0) * scale)))
                acc += w["days"]

    ordered_ids = shape.get("ordered_topic_ids")
    if not isinstance(ordered_ids, list):
        return None
    filtered = [str(t) for t in ordered_ids if str(t) in known_topic_ids]
    if not filtered:
        return None
    shape["ordered_topic_ids"] = filtered

    # Coerce optional sections.
    blitzes = shape.get("weak_blitzes") or []
    clean_blitzes = []
    for b in blitzes:
        if not isinstance(b, dict):
            continue
        tid = str(b.get("topic_id") or "")
        if tid not in known_topic_ids:
            continue
        offsets = [int(o) for o in (b.get("days") or []) if isinstance(o, (int, float)) and 0 <= int(o) < days_available]
        if not offsets:
            continue
        clean_blitzes.append({"topic_id": tid, "days": offsets, "why": b.get("why", "")})
    shape["weak_blitzes"] = clean_blitzes

    diag = shape.get("diagnostic_week") or []
    clean_diag = []
    for d in diag:
        if not isinstance(d, dict):
            continue
        off = int(d.get("day_offset") or 0)
        ids = [str(t) for t in (d.get("topic_ids") or []) if str(t) in known_topic_ids]
        if ids and 0 <= off < days_available:
            clean_diag.append({"day_offset": off, "topic_ids": ids, "n": int(d.get("n") or 20)})
    shape["diagnostic_week"] = clean_diag

    if not isinstance(shape.get("strategy_md"), str):
        shape["strategy_md"] = ""

    return shape


def shape_plan(
    mastery_vector: Dict[str, Dict[str, Any]],
    bundle: Dict[str, Any],
    start_date: date,
    end_date: date,
    hours_per_day: float,
    subscription_status: str,
    daily_activity: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Call Sonnet and return a validated plan shape dict, or None on any
    failure. Callers must fall back to rule-based scheduling when None.
    """
    if not SHAPER_ENABLED:
        return None

    bundle_topics: List[Dict[str, Any]] = []
    for cat in (bundle or {}).get("categories", []) or []:
        for sub in cat.get("subcategories", []) or []:
            for t in sub.get("topics", []) or []:
                bundle_topics.append({
                    **t,
                    "_category_name": cat.get("name"),
                    "_subcategory_name": sub.get("name"),
                })
    if not bundle_topics:
        return None

    days_available = max(1, (end_date - start_date).days + 1)
    known_topic_ids = {str(t.get("_id") or t.get("topic_id") or "") for t in bundle_topics}
    known_topic_ids.discard("")

    compact = _compact_vector_for_prompt(mastery_vector, bundle_topics, max_rows=40)
    catalog = _bundle_topic_catalog(bundle_topics)

    total_lifetime_attempts = sum(r.get("attempted", 0) for r in compact)
    activity = daily_activity or {}

    payload = {
        "plan_window": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days_available": days_available,
            "hours_per_day": hours_per_day,
        },
        "subscription_status": subscription_status,
        "student_stats": {
            "total_mcq_attempts_30d_window": total_lifetime_attempts,
            "avg_minutes_last_14d": int(activity.get("avg_minutes_last_14d") or 0),
            "streak_current": int((activity.get("streak") or {}).get("current") or 0),
            "cold_start": total_lifetime_attempts < 50,
        },
        "mastery_sample": compact,
        "lms_library_topics": catalog,
    }

    prompt = (
        "Design the NEET SS surgery study plan blueprint for this student. "
        "You MUST use only topic_ids that appear in `lms_library_topics`. "
        "The `ordered_topic_ids` list MUST include every topic from the library "
        "(weak/cold first, strong/mature last). Return only the JSON object.\n\n"
        "INPUT:\n```json\n"
        + json.dumps(payload, indent=2, default=str)
        + "\n```\n"
    )

    try:
        res = call_claude_json(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            model=SONNET,
            max_tokens=8000,
            temperature=0.4,
        )
    except ClaudeError as e:
        logger.warning("[shaper] claude call failed: %s", e)
        return None

    shape = res.get("json")
    validated = _validate_shape(shape, days_available, known_topic_ids)
    if validated is None:
        logger.warning("[shaper] validation failed; output=%r", (res.get("text") or "")[:200])
        return None

    validated["_tokens"] = {
        "input": res.get("input_tokens"),
        "output": res.get("output_tokens"),
        "total": res.get("tokens"),
        "model": res.get("model"),
    }
    return validated
