from __future__ import annotations
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from math import floor
from typing import List, Dict, Any, Tuple
from .priorities import ROTATION_ORDER  # optional weighting

IST = ZoneInfo("Asia/Kolkata")

# --------------------------------------------------------------------------- #
#                              UTILITY FUNCTIONS
# --------------------------------------------------------------------------- #

def daterange(start: date, end: date) -> List[date]:
    """Return inclusive list of dates [start, end]."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]

def even_spacing(total_days: int, count: int, offset_start: int, offset_end: int) -> List[int]:
    """Return evenly spaced indices for mock allocation."""
    span = max(0, total_days - offset_start - offset_end)
    if count <= 0 or span <= 0:
        return []
    if count == 1:
        return [offset_start + span // 2]
    step = span / (count - 1)
    return [round(offset_start + i * step) for i in range(count)]

def estimate_mcq_count(mcq_minutes: int, per_q_minutes: float = 2.5) -> int:
    return max(0, floor(mcq_minutes / per_q_minutes))

def spaced_recall_offsets() -> List[int]:
    """Return spaced recall pattern offsets."""
    return [1, 3, 5, 7, 9]

def build_rotation_series(n_days: int) -> List[str]:
    """Rotate topics cyclically for n_days."""
    out = []
    L = len(ROTATION_ORDER)
    for i in range(n_days):
        out.append(ROTATION_ORDER[i % L])
    return out

def make_interleaved(theory_today: str, day_index: int, rotation: List[str]) -> str:
    """Ensure MCQ topic ≠ today's theory topic."""
    for offset in range(1, 3):
        if day_index - offset >= 0:
            prev = rotation[day_index - offset]
            if prev != theory_today:
                return prev
    for t in ROTATION_ORDER:
        if t != theory_today:
            return t
    return theory_today

def recall_for_day(day_index: int, learned_map: Dict[int, str]) -> List[str]:
    """Return topics due for recall based on spaced recall offsets."""
    due = []
    for learned_day, topic in learned_map.items():
        if (day_index - learned_day) in spaced_recall_offsets():
            due.append(topic)
    return due

# --------------------------------------------------------------------------- #
#                             PHASE DISTRIBUTION
# --------------------------------------------------------------------------- #

PHASE_LABELS = {
    "initial70": "📗 Foundation Phase",
    "middle":    "📘 Consolidation Phase",
    "revision":  "📙 Revision Phase",
}

def minutes_split(total_minutes: int, phase_key: str) -> Tuple[int, int, int]:
    """Split total study minutes into theory, MCQ, and recall components."""
    if phase_key == "initial70":
        t = round(total_minutes * 0.35)
        m = round(total_minutes * 0.45)
        r = total_minutes - t - m
    elif phase_key == "middle":
        t = round(total_minutes * 0.25)
        m = round(total_minutes * 0.55)
        r = total_minutes - t - m
    else:  # revision
        t = round(total_minutes * 0.10)
        m = round(total_minutes * 0.40)
        r = total_minutes - t - m
    return t, m, r

def allocate_phases(days_total: int, last15: int = 15) -> Dict[str, Tuple[int, int]]:
    """Return index ranges for foundation, consolidation, and revision."""
    rev_start = max(0, days_total - last15)
    initial_len = round((rev_start) * 0.70)
    middle_start = initial_len
    middle_end = rev_start - 1
    return {
        "initial70": (0, max(-1, initial_len - 1)),
        "middle": (middle_start, middle_end) if middle_start <= middle_end else (-1, -2),
        "revision": (rev_start, days_total - 1),
    }

# --------------------------------------------------------------------------- #
#                              MOCK ALLOCATION
# --------------------------------------------------------------------------- #

def insert_mocks(total_days: int, requested_mocks: int) -> List[int]:
    """Spread mocks evenly between Day 7 and 10 days before the exam."""
    if total_days < 20 or requested_mocks <= 0:
        return []
    first = 7
    last = max(0, total_days - 11)
    if requested_mocks == 1:
        return [first]
    mids = max(0, requested_mocks - 2)
    middle_positions = even_spacing(total_days, mids, first + 5, total_days - 1 - last)
    mock_days = sorted(set([first] + middle_positions + [last]))
    return [min(d, total_days - 11) for d in mock_days]

# --------------------------------------------------------------------------- #
#                             PHASE GUIDANCE
# --------------------------------------------------------------------------- #

FOUNDATION_QUOTES = [
    "Clarity first, speed later.",
    "Strong basics make brilliant surgeons.",
]
CONSOLIDATION_QUOTES = [
    "Accuracy improves when analysis precedes speed.",
    "Your recall is your surgical reflex — refine it.",
]
REVISION_QUOTES = [
    "Mocks don’t judge you — they train you.",
    "Less reading, more reinforcement.",
]

FOCUS_NOTES = {
    "📗 Foundation Phase": [
        "Focus on conceptual clarity and fundamentals.",
        "Spend more time on Bailey, Sabiston & Schwartz theory reading.",
        "Mark weak topics for future spaced recall.",
    ],
    "📘 Consolidation Phase": [
        "Increase MCQ intensity; analyze reasoning errors thoroughly.",
        "Revise recall notes before each MCQ session.",
        "Build endurance with mixed-topic timed practice.",
    ],
    "📙 Revision Phase": [
        "Prioritize rapid revision over new topics.",
        "Simulate exam conditions; full analysis next day.",
        "Focus on the weakest 20% topics relentlessly.",
    ],
}

def pick_quote(phase_label: str, week_num: int) -> str:
    if "Foundation" in phase_label:
        return FOUNDATION_QUOTES[week_num % len(FOUNDATION_QUOTES)]
    if "Consolidation" in phase_label:
        return CONSOLIDATION_QUOTES[week_num % len(CONSOLIDATION_QUOTES)]
    return REVISION_QUOTES[week_num % len(REVISION_QUOTES)]

# --------------------------------------------------------------------------- #
#                             PLAN TYPE FILTER
# --------------------------------------------------------------------------- #

def _apply_plan_type_filter(day_plan: Dict[str, Any], plan_type: str) -> None:
    """Filter out sections based on selected plan_type."""
    if plan_type == "full":
        return
    if plan_type == "theory":
        day_plan["mcq"]["minutes"] = 0
        day_plan["mcq"]["target_questions"] = 0
        day_plan["recall"]["minutes"] = 0
        day_plan.pop("mock", None)
    elif plan_type == "mcq":
        day_plan["theory"]["minutes"] = 0
        day_plan["recall"]["minutes"] = 0
        day_plan.pop("mock", None)
    elif plan_type == "revision":
        day_plan["theory"]["minutes"] = 0
        day_plan["mcq"]["minutes"] = 0
        day_plan["mcq"]["target_questions"] = 0
    elif plan_type == "mock":
        if not day_plan.get("is_mock_day"):
            day_plan["theory"]["minutes"] = 0
            day_plan["mcq"]["minutes"] = 0
            day_plan["recall"]["minutes"] = 0

# --------------------------------------------------------------------------- #
#                             MAIN SCHEDULE BUILDER
# --------------------------------------------------------------------------- #

def build_schedule(
    start_date: date,
    exam_date: date,
    hours_per_day: float,
    mocks: int,
    avg_mcq_minutes: float = 2.5,
    plan_type: str = "full"
) -> Dict[str, Any]:

    if exam_date <= start_date:
        raise ValueError("exam_date must be after start_date")

    days = daterange(start_date, exam_date)
    total_days = len(days)
    phases = allocate_phases(total_days, last15=15)
    rotation = build_rotation_series(total_days)
    learned_map: Dict[int, str] = {}
    mock_days = set(insert_mocks(total_days, mocks))

    per_day_minutes = round(hours_per_day * 60)
    out_days: List[Dict[str, Any]] = []

    for i, d in enumerate(days):
        # Determine phase
        if phases["revision"][0] <= i <= phases["revision"][1]:
            phase_key = "revision"
        elif phases["initial70"][0] <= i <= phases["initial70"][1]:
            phase_key = "initial70"
        else:
            phase_key = "middle"

        phase_label = PHASE_LABELS[phase_key]
        theory_min, mcq_min, recall_min = minutes_split(per_day_minutes, phase_key)

        theory_topic = rotation[i]
        mcq_topic = make_interleaved(theory_topic, i, rotation)
        recalls = recall_for_day(i, learned_map)
        mcq_target = estimate_mcq_count(mcq_min, avg_mcq_minutes)
        learned_map[i] = theory_topic

        # Base day plan
        day_plan: Dict[str, Any] = {
            "date": d.isoformat(),
            "phase": phase_label,
            "is_mock_day": i in mock_days,
            "theory": {"topic": theory_topic, "minutes": theory_min},
            "mcq": {
                "topic": mcq_topic,
                "minutes": mcq_min,
                "target_questions": mcq_target,
                "avg_minutes_per_mcq": avg_mcq_minutes
            },
            "recall": {"due_topics": recalls, "minutes": recall_min, "scheme": "1-3-5-7-9 days"},
        }

        # Insert mock details
        if i in mock_days:
            mock_minutes = min(per_day_minutes, 150)
            analysis_minutes = max(0, per_day_minutes - mock_minutes)
            day_plan["mock"] = {
                "minutes": mock_minutes,
                "analysis_minutes": analysis_minutes,
                "notes": "Full-length mock; analyze ~20 wrong answers; tag weak topics."
            }
            day_plan["theory"]["minutes"] = 0
            day_plan["mcq"]["minutes"] = 0
            day_plan["recall"]["minutes"] = max(30, day_plan["recall"]["minutes"])

        _apply_plan_type_filter(day_plan, plan_type)
        out_days.append(day_plan)

    # Weekly summaries
    weeks: List[Dict[str, Any]] = []
    for w_idx, w_start in enumerate(range(0, total_days, 7), start=1):
        block = out_days[w_start:w_start + 7]
        phase_label = block[0]["phase"]
        theory_min = sum(d["theory"]["minutes"] for d in block)
        mcq_min = sum(d["mcq"]["minutes"] for d in block)
        recall_min = sum(d["recall"]["minutes"] for d in block)
        approx_mcqs = estimate_mcq_count(mcq_min, avg_mcq_minutes)

        weeks.append({
            "week": w_idx,
            "phase": phase_label,
            "start_date": block[0]["date"],
            "end_date": block[-1]["date"],
            "focus_notes": FOCUS_NOTES.get(phase_label, []),
            "quote": pick_quote(phase_label, w_idx),
            "weekly_targets": {
                "theory_min": theory_min,
                "mcq_min": mcq_min,
                "recall_min": recall_min,
                "approx_mcqs": approx_mcqs
            }
        })

    # Final plan output
    return {
        "meta": {
            "start_date": start_date.isoformat(),
            "exam_date": exam_date.isoformat(),
            "days": total_days,
            "hours_per_day": hours_per_day,
            "plan_type": plan_type,
            "mock_days_indexed": sorted(list(mock_days)),
            "timezone": "Asia/Kolkata"
        },
        "schedule": out_days,
        "weekly_summaries": weeks
    }
