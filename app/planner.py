from __future__ import annotations
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from math import floor
from typing import List, Dict, Any, Tuple
from .priorities import PRIORITY_DISTRIBUTION, ROTATION_ORDER

IST = ZoneInfo("Asia/Kolkata")


# ---------------------------- Utility Functions ---------------------------- #

def daterange(start: date, end: date) -> List[date]:
    """Return inclusive date list."""
    days = (end - start).days
    return [start + timedelta(days=i) for i in range(days + 1)]


def even_spacing(total_days: int, count: int, offset_start: int, offset_end: int) -> List[int]:
    """Return day indices (0-based) for evenly spaced items within [offset_start, total_days-1-offset_end]."""
    span = max(0, total_days - offset_start - offset_end)
    if count <= 0 or span <= 0:
        return []
    if count == 1:
        return [offset_start + span // 2]
    step = span / (count - 1)
    return [round(offset_start + i * step) for i in range(count)]


def minutes_split(total_minutes: int, phase: str) -> Tuple[int, int, int]:
    """Return (theory_min, mcq_min, recall_min) according to the phase."""
    if phase == "initial70":
        return round(total_minutes*0.35), round(total_minutes*0.45), total_minutes - round(total_minutes*0.35) - round(total_minutes*0.45)
    if phase == "middle":
        return round(total_minutes*0.25), round(total_minutes*0.55), total_minutes - round(total_minutes*0.25) - round(total_minutes*0.55)
    # revision phase
    return round(total_minutes*0.10), round(total_minutes*0.40), total_minutes - round(total_minutes*0.10) - round(total_minutes*0.40)


def estimate_mcq_count(mcq_minutes: int, per_q_minutes: float = 2.5) -> int:
    return max(0, floor(mcq_minutes / per_q_minutes))


def spaced_recall_offsets() -> List[int]:
    """1–3–5–7–9 day recall pattern."""
    return [1, 3, 5, 7, 9]


def build_rotation_series(n_days: int) -> List[str]:
    """Rotate topics through ROTATION_ORDER for n_days."""
    out = []
    idx = 0
    L = len(ROTATION_ORDER)
    for _ in range(n_days):
        out.append(ROTATION_ORDER[idx % L])
        idx += 1
    return out


def make_interleaved(theory_today: str, day_index: int, rotation: List[str]) -> str:
    """Ensure MCQ topic ≠ theory topic, pick from earlier theory topic."""
    if day_index >= 1:
        prev = rotation[day_index - 1]
        if prev != theory_today:
            return prev
    if day_index >= 2:
        prev2 = rotation[day_index - 2]
        if prev2 != theory_today:
            return prev2
    # fallback
    for t in ROTATION_ORDER:
        if t != theory_today:
            return t
    return theory_today


def recall_for_day(day_index: int, learned_map: Dict[int, str]) -> List[str]:
    """Return recall topics due today based on 1–3–5–7–9 rule."""
    due = []
    for learned_day, topic in learned_map.items():
        if day_index - learned_day in spaced_recall_offsets():
            due.append(topic)
    return due


def allocate_phases(days_total: int, last15: int = 15) -> Dict[str, Tuple[int, int]]:
    """Allocate study days into initial70, middle, revision."""
    rev_start = max(0, days_total - last15)
    pre_rev = rev_start
    initial_len = round(pre_rev * 0.70)
    middle_start = initial_len
    middle_end = pre_rev - 1
    return {
        "initial70": (0, max(-1, initial_len - 1)),
        "middle": (middle_start, middle_end) if middle_start <= middle_end else (-1, -2),
        "revision": (rev_start, days_total - 1)
    }


# --------------------------- Mock Day Allocation --------------------------- #

def insert_mocks(total_days: int, requested_mocks: int) -> List[int]:
    """
    Place first mock after 7 days and last mock ~10 days before exam.
    Ensure ≥3-day gap between any two mocks.
    """
    if total_days < 20 or requested_mocks <= 0:
        return []

    first = 7
    last = max(0, total_days - 11)

    if requested_mocks == 1:
        return [first]

    mids = max(0, requested_mocks - 2)
    middle_positions = even_spacing(total_days, mids, first + 5, total_days - 1 - last)

    # Combine
    raw_mocks = [first] + middle_positions + [last]
    mock_days = []

    for d in sorted(raw_mocks):
        if not mock_days or d - mock_days[-1] >= 3:
            mock_days.append(d)
        else:
            mock_days.append(mock_days[-1] + 3)  # push forward to maintain gap

    # Ensure last mock within limit
    mock_days = [min(d, total_days - 11) for d in mock_days]
    return sorted(set(mock_days))


# ---------------------------- Schedule Builder ---------------------------- #

def build_schedule(
    start_date: date,
    exam_date: date,
    hours_per_day: float,
    mocks: int,
    avg_mcq_minutes: float = 2.5
) -> Dict[str, Any]:

    days = daterange(start_date, exam_date)
    total_days = len(days)
    phases = allocate_phases(total_days, last15=15)

    rotation = build_rotation_series(total_days)
    learned_map: Dict[int, str] = {}

    # mock placement
    mock_days = insert_mocks(total_days, mocks)
    mock_set = set(mock_days)

    per_day_minutes = round(hours_per_day * 60)
    out_days: List[Dict[str, Any]] = []

    for i, d in enumerate(days):
        # Determine phase
        if phases["revision"][0] <= i <= phases["revision"][1]:
            phase = "revision"
        elif phases["initial70"][0] <= i <= phases["initial70"][1]:
            phase = "initial70"
        else:
            phase = "middle"

        theory_min, mcq_min, recall_min = minutes_split(per_day_minutes, phase)

        theory_topic = rotation[i]
        mcq_topic = make_interleaved(theory_topic, i, rotation)
        recalls = recall_for_day(i, learned_map)
        mcq_target = estimate_mcq_count(mcq_min, avg_mcq_minutes)
        learned_map[i] = theory_topic

        day_plan = {
            "date": d.isoformat(),
            "phase": phase,
            "is_mock_day": i in mock_set,
            "theory": {"topic": theory_topic, "minutes": theory_min},
            "mcq": {"topic": mcq_topic, "minutes": mcq_min, "target_questions": mcq_target, "avg_minutes_per_mcq": avg_mcq_minutes},
            "recall": {"due_topics": recalls, "minutes": recall_min, "scheme": "1-3-5-7-9 days"}
        }

        if i in mock_set:
            # Mock day → only mock + recall
            mock_minutes = min(per_day_minutes, 150)
            analysis_minutes = per_day_minutes - mock_minutes
            day_plan["mock"] = {
                "minutes": mock_minutes,
                "analysis_minutes": analysis_minutes,
                "notes": "Full-length mock; analyze 20 wrong answers; tag weak topics."
            }
            day_plan["theory"]["minutes"] = 0
            day_plan["mcq"]["minutes"] = 0
            day_plan["recall"]["minutes"] = max(30, recall_min)

        out_days.append(day_plan)

    # ---------------------- Weekly Summaries ---------------------- #
    weeks: List[Dict[str, Any]] = []
    for w_start in range(0, total_days, 7):
        w_end = min(total_days, w_start + 7)
        block = out_days[w_start:w_end]
        topics = [d["theory"]["topic"] for d in block if d["theory"]["minutes"] > 0]
        mcq_topics = [d["mcq"]["topic"] for d in block if d["mcq"]["minutes"] > 0]
        mocks_in_week = sum(1 for d in block if d["is_mock_day"])
        week_num = len(weeks) + 1

        # Determine weekly phase type
        total_weeks = (total_days + 6) // 7
        if week_num <= total_weeks / 3:
            phase_name = "📗 Foundation Phase"
            tips = [
                "Focus on conceptual clarity and understanding of fundamentals.",
                "Spend more time on theory reading (Bailey, Sabiston, Schwartz).",
                "Mark difficult topics for spaced recall and future reinforcement."
            ]
        elif week_num <= (2 * total_weeks) / 3:
            phase_name = "📘 Consolidation Phase"
            tips = [
                "Increase MCQ practice frequency and analyze reasoning errors.",
                "Revise recall notes before solving daily MCQs.",
                "Build speed and exam endurance through mixed topic practice."
            ]
        else:
            phase_name = "📙 Revision Phase"
            tips = [
                "Focus on rapid review and mock analysis.",
                "Simulate real exam conditions once a week.",
                "Avoid new topics; reinforce weak areas identified earlier."
            ]

        weeks.append({
            "week": week_num,
            "start_date": block[0]["date"],
            "end_date": block[-1]["date"],
            "phase": phase_name,
            "theory_topics": topics,
            "mcq_topics": mcq_topics,
            "mocks": mocks_in_week,
            "suggestions": tips
        })

    return {
        "meta": {
            "start_date": start_date.isoformat(),
            "exam_date": exam_date.isoformat(),
            "days": total_days,
            "hours_per_day": hours_per_day,
            "mock_days_indexed": sorted(list(mock_set)),
            "timezone": "Asia/Kolkata"
        },
        "schedule": out_days,
        "weekly_summaries": weeks
    }
