from __future__ import annotations
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from math import floor
from typing import List, Dict, Any, Tuple
from .priorities import PRIORITY_DISTRIBUTION, ROTATION_ORDER

IST = ZoneInfo("Asia/Kolkata")

def daterange(start: date, end: date) -> List[date]:
    # end is inclusive
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
    """
    Returns (theory_min, mcq_min, recall_min).
    Phases:
      - 'initial70': 35/45/20
      - 'middle':    25/55/20 (consolidation)
      - 'revision':  10/40/50 (safety cap; last 15 days mostly recall)
    """
    if phase == "initial70":
        return round(total_minutes*0.35), round(total_minutes*0.45), total_minutes - round(total_minutes*0.35) - round(total_minutes*0.45)
    if phase == "middle":
        return round(total_minutes*0.25), round(total_minutes*0.55), total_minutes - round(total_minutes*0.25) - round(total_minutes*0.55)
    # revision block
    return round(total_minutes*0.10), round(total_minutes*0.40), total_minutes - round(total_minutes*0.10) - round(total_minutes*0.40)

def estimate_mcq_count(mcq_minutes: int, per_q_minutes: float = 2.5) -> int:
    return max(0, floor(mcq_minutes / per_q_minutes))

def spaced_recall_offsets() -> List[int]:
    # 1-3-5-7-9 day rule measured from the learning day (D0)
    return [1, 3, 5, 7, 9]

def build_rotation_series(n_days: int) -> List[str]:
    out = []
    idx = 0
    L = len(ROTATION_ORDER)
    for _ in range(n_days):
        out.append(ROTATION_ORDER[idx % L])
        idx += 1
    return out

def make_interleaved(theory_today: str, day_index: int, rotation: List[str]) -> str:
    """
    Ensure MCQ topic != today's theory topic by taking the topic used as theory 1–2 days earlier.
    If not available yet (early days), pick next in rotation that is != theory_today.
    """
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
    return theory_today  # worst case

def recall_for_day(day_index: int, learned_map: Dict[int, str]) -> List[str]:
    """
    Topics due for recall today based on earlier learning with 1-3-5-7-9 spacing.
    """
    due = []
    for learned_day, topic in learned_map.items():
        if day_index - learned_day in spaced_recall_offsets():
            due.append(topic)
    return due

def allocate_phases(days_total: int, last15: int = 15) -> Dict[str, Tuple[int, int]]:
    """
    Returns index ranges (start_idx, end_idx inclusive) for:
      initial70, middle, revision (last 15 days).
    """
    rev_start = max(0, days_total - last15)
    # remaining before revision
    pre_rev = rev_start
    initial_len = round(pre_rev * 0.70)
    middle_start = initial_len
    middle_end = pre_rev - 1
    return {
        "initial70": (0, max(-1, initial_len - 1)),
        "middle": (middle_start, middle_end) if middle_start <= middle_end else (-1, -2),
        "revision": (rev_start, days_total - 1)
    }

def insert_mocks(total_days: int, requested_mocks: int) -> List[int]:
    """
    Place first mock @ +7d, last mock @ -10d, balance evenly between.
    Return list of day indices.
    """
    if total_days < 20 or requested_mocks <= 0:
        return []
    # Guarantee at least 2 if requested >=2 and feasible
    first = 7
    last = max(0, total_days - 11)
    requested_mocks = min(requested_mocks, max(2, requested_mocks))
    if requested_mocks == 1:
        return [first]
    if last <= first:
        return [first]
    mids = max(0, requested_mocks - 2)
    middle_positions = even_spacing(total_days, mids, first+1, total_days-1-last)
    return sorted([first] + middle_positions + [last])

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
    learned_map: Dict[int, str] = {}  # day_index -> topic learned that day (for recall mapping)

    # mock placement
    mock_days = insert_mocks(total_days, mocks)
    mock_set = set(mock_days)

    per_day_minutes = round(hours_per_day * 60)
    out_days: List[Dict[str, Any]] = []

    for i, d in enumerate(days):
        # decide phase
        if phases["revision"][0] <= i <= phases["revision"][1]:
            phase = "revision"
        elif phases["initial70"][0] <= i <= phases["initial70"][1]:
            phase = "initial70"
        else:
            phase = "middle"

        theory_min, mcq_min, recall_min = minutes_split(per_day_minutes, phase)

        # topic assignment
        theory_topic = rotation[i]
        mcq_topic = make_interleaved(theory_topic, i, rotation)

        # recall due
        recalls = recall_for_day(i, learned_map)

        # mcq target (2–3 min/q; default 2.5)
        mcq_target = estimate_mcq_count(mcq_min, avg_mcq_minutes)

        # mark theory topic as "learned today" for future recall
        learned_map[i] = theory_topic

        day_plan = {
            "date": d.isoformat(),
            "phase": phase,
            "is_mock_day": i in mock_set,
            "theory": {
                "topic": theory_topic,
                "minutes": theory_min
            },
            "mcq": {
                "topic": mcq_topic,
                "minutes": mcq_min,
                "target_questions": mcq_target,
                "avg_minutes_per_mcq": avg_mcq_minutes
            },
            "recall": {
                "due_topics": recalls,
                "minutes": recall_min,
                "scheme": "1-3-5-7-9 days"
            }
        }

        if i in mock_set:
            # Dedicate bulk time to mock + analysis; reallocate conservatively
            mock_minutes = min(per_day_minutes, 150)  # typical 150-min mock
            analysis_minutes = per_day_minutes - mock_minutes
            # Keep a sliver of recall alive
            day_plan["mock"] = {
                "minutes": mock_minutes,
                "analysis_minutes": analysis_minutes,
                "notes": "Full-length mock; analyze top 20 wrong answers; tag weak topics to priority queue."
            }
            # Slightly trim theory/mcq for mock day
            day_plan["theory"]["minutes"] = max(0, theory_min // 3)
            day_plan["mcq"]["minutes"] = max(0, mcq_min // 3)
            day_plan["recall"]["minutes"] = max(15, recall_min // 2)

        out_days.append(day_plan)

    # Weekly summaries
    weeks: List[Dict[str, Any]] = []
    for w_start in range(0, total_days, 7):
        w_end = min(total_days, w_start + 7)
        block = out_days[w_start:w_end]
        topics = [d["theory"]["topic"] for d in block]
        mcq_topics = [d["mcq"]["topic"] for d in block]
        mocks_in_week = sum(1 for d in block if d["is_mock_day"])
        weeks.append({
            "week": len(weeks) + 1,
            "start_date": block[0]["date"],
            "end_date": block[-1]["date"],
            "theory_topics": topics,
            "mcq_topics": mcq_topics,
            "mocks": mocks_in_week,
            "suggestions": [
                "Prioritize weak topics surfaced by recent mocks.",
                "Ensure recall completion ≥ 80% for due items.",
                "Keep interleaving: theory ≠ MCQ topic; avoid same-day pairing."
            ]
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
