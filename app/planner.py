from __future__ import annotations
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from math import floor
from typing import List, Dict, Any, Tuple, Optional, Set
from .priorities import (
    ROTATION_ORDER, get_topic_priority, get_subtopic_for_day,
    get_subtopics, SYLLABUS_TREE, sm2_next_interval,
)

# --------------------------------------------------------------------------- #
#                              CONFIGURATION
# --------------------------------------------------------------------------- #

IST = ZoneInfo("Asia/Kolkata")

PHASE_LABELS = {
    "initial70": "📗 Foundation Phase",
    "middle": "📘 Consolidation Phase",
    "revision": "📙 Revision Phase",
}

FOUNDATION_QUOTES = [
    "Clarity first, speed later.",
    "Strong basics make brilliant surgeons.",
    "The foundation you build today carries your career tomorrow.",
    "Read to understand, not to finish chapters.",
    "One topic mastered beats ten topics skimmed.",
    "Deliberate practice separates good from great.",
]
CONSOLIDATION_QUOTES = [
    "Accuracy improves when analysis precedes speed.",
    "Your recall is your surgical reflex — refine it.",
    "Every wrong MCQ is a lesson you won't forget.",
    "Pattern recognition starts with deliberate practice.",
    "The questions you struggle with are the ones worth solving.",
    "Consistency compounds faster than intensity.",
]
REVISION_QUOTES = [
    "Mocks don't judge you — they train you.",
    "Less reading, more reinforcement.",
    "Trust your preparation. Execute with precision.",
    "The last mile demands the strongest discipline.",
    "Revision is not repetition — it's reinforcement.",
    "Your weakest topics deserve your strongest effort now.",
]

FOCUS_NOTES = {
    "📗 Foundation Phase": [
        "Focus on conceptual clarity and fundamentals.",
        "Spend more time on Bailey, Sabiston & Schwartz theory reading.",
        "Mark weak topics for future spaced recall.",
        "Aim for understanding over memorization.",
    ],
    "📘 Consolidation Phase": [
        "Increase MCQ intensity; analyze reasoning errors thoroughly.",
        "Revise recall notes before each MCQ session.",
        "Build endurance with mixed-topic timed practice.",
        "Track your accuracy — aim for 70%+ on known topics.",
    ],
    "📙 Revision Phase": [
        "Prioritize rapid revision over new topics.",
        "Simulate exam conditions; full analysis next day.",
        "Focus on the weakest 20% topics relentlessly.",
        "Review high-yield images, tables, and algorithms.",
    ],
}

# --------------------------------------------------------------------------- #
#                              UTILITY FUNCTIONS
# --------------------------------------------------------------------------- #


def daterange(start: date, end: date) -> List[date]:
    """Return inclusive list of dates [start, end]."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def even_spacing(total_days: int, count: int, offset_start: int, offset_end: int) -> List[int]:
    """Return evenly spaced indices between given offsets."""
    span = max(0, total_days - offset_start - offset_end)
    if count <= 0 or span <= 0:
        return []
    if count == 1:
        return [offset_start + span // 2]
    step = span / (count - 1)
    return [round(offset_start + i * step) for i in range(count)]


def estimate_mcq_count(mcq_hours: float, per_q_minutes: float = 2.5) -> int:
    """Estimate MCQs based on hours and avg time per question."""
    total_minutes = mcq_hours * 60
    return max(0, floor(total_minutes / per_q_minutes))


def build_weighted_rotation(
    n_days: int,
    custom_weights: Optional[Dict[str, float]] = None,
    weakness_boost: Optional[Dict[str, float]] = None,
    selected_topics: Optional[List[str]] = None,
) -> List[str]:
    """
    Build topic rotation with priority weighting.

    If selected_topics is provided, ONLY those topics are used and
    their list order determines priority (first = highest weight).
    Otherwise, the full SYLLABUS_TREE with P1/P2/P3 weights is used.
    """
    topics_scored: List[Tuple[str, float]] = []

    if selected_topics and len(selected_topics) > 0:
        # Student-chosen ordering: first topic gets highest positional weight
        total = len(selected_topics)
        for rank, topic in enumerate(selected_topics):
            if topic not in SYLLABUS_TREE:
                continue
            data = SYLLABUS_TREE[topic]
            # Position weight: first topic = 3.0, last = 1.0
            position_mult = 3.0 - (rank / max(1, total - 1)) * 2.0 if total > 1 else 3.0
            base_weight = data["weight"]
            mult = position_mult

            if custom_weights and topic in custom_weights:
                mult *= custom_weights[topic]
            if weakness_boost and topic in weakness_boost:
                mult *= (1.0 + weakness_boost[topic])

            topics_scored.append((topic, base_weight * mult))
    else:
        # Default: use full syllabus with P1/P2/P3 priority multipliers
        for topic, data in SYLLABUS_TREE.items():
            base_weight = data["weight"]

            if data["priority"] == "P1_HIGH":
                mult = 3.0
            elif data["priority"] == "P2_MODERATE":
                mult = 2.0
            else:
                mult = 1.0

            if custom_weights and topic in custom_weights:
                mult *= custom_weights[topic]
            if weakness_boost and topic in weakness_boost:
                mult *= (1.0 + weakness_boost[topic])

            topics_scored.append((topic, base_weight * mult))

    # Sort by weight descending
    topics_scored.sort(key=lambda x: -x[1])

    # Build rotation proportional to weights
    total_weight = sum(w for _, w in topics_scored)
    rotation: List[str] = []
    for topic, weight in topics_scored:
        count = max(1, round((weight / total_weight) * n_days))
        rotation.extend([topic] * count)

    # Trim or extend to exact n_days
    while len(rotation) < n_days:
        rotation.extend([t for t, _ in topics_scored])
    rotation = rotation[:n_days]

    # Shuffle to avoid long runs of same topic, but keep deterministic
    import hashlib
    seed = int(hashlib.md5(str(n_days).encode()).hexdigest()[:8], 16)
    rng_state = seed
    for i in range(len(rotation) - 1, 0, -1):
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        j = rng_state % (i + 1)
        rotation[i], rotation[j] = rotation[j], rotation[i]

    # Ensure no 3 consecutive same topics
    for i in range(2, len(rotation)):
        if rotation[i] == rotation[i-1] == rotation[i-2]:
            for j in range(i+1, len(rotation)):
                if rotation[j] != rotation[i]:
                    rotation[i], rotation[j] = rotation[j], rotation[i]
                    break

    return rotation


def make_interleaved(theory_today: str, day_index: int, rotation: List[str]) -> str:
    """Select MCQ topic different from today's theory topic (interleaved practice)."""
    for offset in range(1, 4):
        if day_index - offset >= 0:
            prev = rotation[day_index - offset]
            if prev != theory_today:
                return prev
    # Fallback: pick any other topic from the rotation
    unique_in_rotation = list(set(rotation))
    for t in unique_in_rotation:
        if t != theory_today:
            return t
    return theory_today


# --------------------------------------------------------------------------- #
#                             PHASE DISTRIBUTION
# --------------------------------------------------------------------------- #


def hours_split(total_hours: float, phase_key: str) -> Tuple[float, float, float]:
    """Split total study hours into (theory, MCQ, recall) by phase."""
    if phase_key == "initial70":
        t, m = total_hours * 0.35, total_hours * 0.45
    elif phase_key == "middle":
        t, m = total_hours * 0.25, total_hours * 0.55
    else:  # revision
        t, m = total_hours * 0.10, total_hours * 0.40
    r = total_hours - t - m
    return round(t, 1), round(m, 1), round(r, 1)


def allocate_phases(days_total: int, last15: int = 15, revision_rounds: int = 1) -> Dict[str, Tuple[int, int]]:
    """Return day-index ranges for foundation, consolidation, and revision phases.

    revision_rounds (1-5) controls how many passes through topics happen in revision.
    More rounds = larger revision phase = smaller foundation/consolidation phases.
    """
    # Scale revision days: base 15, each extra round adds ~10 days (capped at 60% of total)
    revision_days = min(
        round(days_total * 0.6),
        last15 + (revision_rounds - 1) * max(10, round(days_total * 0.08))
    )
    revision_days = max(last15, revision_days)

    rev_start = max(0, days_total - revision_days)
    initial_len = round(rev_start * 0.70)
    middle_start = initial_len
    middle_end = rev_start - 1
    return {
        "initial70": (0, max(-1, initial_len - 1)),
        "middle": (middle_start, middle_end) if middle_start <= middle_end else (-1, -2),
        "revision": (rev_start, days_total - 1),
        "revision_rounds": revision_rounds,
    }


# --------------------------------------------------------------------------- #
#                              REST DAYS
# --------------------------------------------------------------------------- #


def compute_rest_days(
    total_days: int,
    rest_per_week: int = 0,
    custom_rest_dates: Optional[List[str]] = None,
    start_date: Optional[date] = None,
) -> Set[int]:
    """
    Compute day indices that are rest days.
    rest_per_week: 0-2 rest days per week (e.g., 1 = every Sunday)
    custom_rest_dates: specific ISO date strings to mark as rest
    """
    rest_indices: Set[int] = set()

    # Weekly rest days
    if rest_per_week > 0 and start_date:
        for i in range(total_days):
            d = start_date + timedelta(days=i)
            # Sunday = 6, Saturday = 5
            if rest_per_week >= 1 and d.weekday() == 6:
                rest_indices.add(i)
            if rest_per_week >= 2 and d.weekday() == 5:
                rest_indices.add(i)

    # Custom rest dates
    if custom_rest_dates and start_date:
        for ds in custom_rest_dates:
            try:
                rd = date.fromisoformat(ds)
                idx = (rd - start_date).days
                if 0 <= idx < total_days:
                    rest_indices.add(idx)
            except ValueError:
                pass

    return rest_indices


# --------------------------------------------------------------------------- #
#                              MOCK ALLOCATION
# --------------------------------------------------------------------------- #


def insert_mocks(total_days: int, requested_mocks: int) -> List[int]:
    """Evenly distribute mocks between day 7 and 10 days before exam."""
    if total_days < 20 or requested_mocks <= 0:
        return []
    first, last = 7, max(0, total_days - 11)
    if requested_mocks == 1:
        return [first]
    mids = max(0, requested_mocks - 2)
    middle_positions = even_spacing(total_days, mids, first + 5, total_days - 1 - last)
    return sorted(set([first] + middle_positions + [last]))


# --------------------------------------------------------------------------- #
#                             QUOTES & NOTES
# --------------------------------------------------------------------------- #


def pick_quote(phase_label: str, week_num: int) -> str:
    """Return a motivational quote based on the phase."""
    if "Foundation" in phase_label:
        return FOUNDATION_QUOTES[week_num % len(FOUNDATION_QUOTES)]
    if "Consolidation" in phase_label:
        return CONSOLIDATION_QUOTES[week_num % len(CONSOLIDATION_QUOTES)]
    return REVISION_QUOTES[week_num % len(REVISION_QUOTES)]


# --------------------------------------------------------------------------- #
#                             MAIN SCHEDULE BUILDER
# --------------------------------------------------------------------------- #


def build_schedule(
    start_date: date,
    exam_date: date,
    hours_per_day: float,
    mocks: int,
    avg_mcq_minutes: float = 2.5,
    rest_per_week: int = 0,
    custom_rest_dates: Optional[List[str]] = None,
    custom_weights: Optional[Dict[str, float]] = None,
    weakness_data: Optional[Dict[str, float]] = None,
    selected_topics: Optional[List[str]] = None,
    revision_rounds: int = 1,
) -> Dict[str, Any]:
    """
    Generate a comprehensive NEET SS study plan.

    selected_topics: ordered list of topics (first = highest priority).
                     If None, all syllabus topics are used.
    revision_rounds: number of revision passes (1-5). Higher = more revision days.

    Returns a dict with:
      - meta: plan metadata
      - schedule: list of daily plans
      - weekly_summaries: weekly aggregated targets and notes
      - syllabus_coverage: topic/subtopic mapping
    """

    if exam_date <= start_date:
        raise ValueError("exam_date must be after start_date")

    days = daterange(start_date, exam_date)
    total_days = len(days)
    phases = allocate_phases(total_days, last15=15, revision_rounds=revision_rounds)

    # Build weighted rotation considering custom weights, weakness, and selected topics
    rotation = build_weighted_rotation(
        total_days,
        custom_weights=custom_weights,
        weakness_boost=weakness_data,
        selected_topics=selected_topics,
    )

    # Rest days
    rest_days = compute_rest_days(total_days, rest_per_week, custom_rest_dates, start_date)

    learned_map: Dict[int, str] = {}
    mock_days = set(insert_mocks(total_days, mocks))
    out_days: List[Dict[str, Any]] = []

    # Track subtopic cycling per topic
    topic_sub_counter: Dict[str, int] = {}

    # ----------------------- Daily Plan Construction ----------------------- #
    for i, d in enumerate(days):
        # REST DAY
        if i in rest_days:
            out_days.append({
                "date": d.isoformat(),
                "day_number": i + 1,
                "phase": "🛌 Rest Day",
                "is_rest_day": True,
                "is_mock_day": False,
                "theory": {"topic": "—", "subtopic": "—", "ref": "", "priority": "—", "hours": 0},
                "mcq": {"topic": "—", "subtopic": "—", "priority": "—", "hours": 0, "target_questions": 0, "avg_minutes_per_mcq": avg_mcq_minutes},
                "recall": {"due_topics": [], "hours": 0, "scheme": "SM-2 adaptive"},
            })
            continue

        # Determine current phase
        if phases["revision"][0] <= i <= phases["revision"][1]:
            phase_key = "revision"
        elif phases["initial70"][0] <= i <= phases["initial70"][1]:
            phase_key = "initial70"
        else:
            phase_key = "middle"

        phase_label = PHASE_LABELS[phase_key]
        theory_hr, mcq_hr, recall_hr = hours_split(hours_per_day, phase_key)

        theory_topic = rotation[i]
        mcq_topic = make_interleaved(theory_topic, i, rotation)

        # Get specific subtopic for this day
        topic_sub_counter.setdefault(theory_topic, 0)
        theory_sub = get_subtopic_for_day(theory_topic, topic_sub_counter[theory_topic])
        topic_sub_counter[theory_topic] += 1

        topic_sub_counter.setdefault(mcq_topic, 0)
        mcq_sub = get_subtopic_for_day(mcq_topic, topic_sub_counter.get(mcq_topic, 0))

        # SM-2 recall: topics due based on learning map
        recalls = []
        for learned_day, topic in learned_map.items():
            gap = i - learned_day
            # Default intervals: 1, 3, 7, 14, 30
            if gap in [1, 3, 7, 14, 30]:
                recalls.append(topic)

        mcq_target = estimate_mcq_count(mcq_hr, avg_mcq_minutes)
        learned_map[i] = theory_topic

        day_plan: Dict[str, Any] = {
            "date": d.isoformat(),
            "day_number": i + 1,
            "phase": phase_label,
            "is_rest_day": False,
            "is_mock_day": i in mock_days,
            "theory": {
                "topic": theory_topic,
                "subtopic": theory_sub["name"],
                "ref": theory_sub["ref"],
                "priority": get_topic_priority(theory_topic),
                "hours": theory_hr,
            },
            "mcq": {
                "topic": mcq_topic,
                "subtopic": mcq_sub["name"],
                "priority": get_topic_priority(mcq_topic),
                "hours": mcq_hr,
                "target_questions": mcq_target,
                "avg_minutes_per_mcq": avg_mcq_minutes,
            },
            "recall": {
                "due_topics": recalls[:5],  # Cap at 5 per day
                "hours": recall_hr,
                "scheme": "SM-2 adaptive",
            },
        }

        # Insert mock if applicable
        if i in mock_days:
            mock_hr = min(hours_per_day, 2.5)
            analysis_hr = max(0.5, hours_per_day - mock_hr)
            day_plan["mock"] = {
                "hours": round(mock_hr, 1),
                "analysis_hours": round(analysis_hr, 1),
                "notes": "Full-length mock; analyze ~20 wrong answers; tag weak topics.",
            }
            day_plan["theory"]["hours"] = 0
            day_plan["mcq"]["hours"] = 0
            day_plan["recall"]["hours"] = round(max(0.5, recall_hr), 1)

        out_days.append(day_plan)

    # ----------------------- Weekly Summaries ----------------------------- #
    weeks: List[Dict[str, Any]] = []
    for w_idx, w_start in enumerate(range(0, total_days, 7), start=1):
        block = out_days[w_start: w_start + 7]
        active_block = [d for d in block if not d.get("is_rest_day")]
        phase_label = active_block[0]["phase"] if active_block else block[0]["phase"]
        theory_hr = sum(d["theory"]["hours"] for d in block)
        mcq_hr = sum(d["mcq"]["hours"] for d in block)
        recall_hr = sum(d["recall"]["hours"] for d in block)
        approx_mcqs = estimate_mcq_count(mcq_hr, avg_mcq_minutes)
        mock_count = sum(1 for d in block if d.get("is_mock_day"))
        rest_count = sum(1 for d in block if d.get("is_rest_day"))

        # Topics covered this week
        week_topics = list(set(
            d["theory"]["topic"] for d in active_block
            if d["theory"]["topic"] != "—"
        ))

        weeks.append({
            "week": w_idx,
            "phase": phase_label,
            "start_date": block[0]["date"],
            "end_date": block[-1]["date"],
            "focus_notes": FOCUS_NOTES.get(phase_label, []),
            "quote": pick_quote(phase_label, w_idx),
            "mocks": mock_count,
            "rest_days": rest_count,
            "topics_covered": week_topics,
            "weekly_targets": {
                "theory_hr": round(theory_hr, 1),
                "mcq_hr": round(mcq_hr, 1),
                "recall_hr": round(recall_hr, 1),
                "approx_mcqs": approx_mcqs,
                "study_days": len(active_block),
            },
        })

    # ----------------------- Syllabus Coverage Map ----------------------- #
    coverage: Dict[str, List[str]] = {}
    for day in out_days:
        if day.get("is_rest_day"):
            continue
        t = day["theory"]["topic"]
        sub = day["theory"].get("subtopic", "")
        if t not in coverage:
            coverage[t] = []
        if sub and sub not in coverage[t] and sub != "—":
            coverage[t].append(sub)

    # ----------------------- Final Output -------------------------------- #
    return {
        "meta": {
            "start_date": start_date.isoformat(),
            "exam_date": exam_date.isoformat(),
            "days": total_days,
            "study_days": total_days - len(rest_days),
            "rest_days": len(rest_days),
            "hours_per_day": hours_per_day,
            "mock_days_indexed": sorted(list(mock_days)),
            "rest_days_indexed": sorted(list(rest_days)),
            "timezone": "Asia/Kolkata",
            "version": "3.1.0",
            "selected_topics": selected_topics or list(SYLLABUS_TREE.keys()),
            "revision_rounds": revision_rounds,
        },
        "schedule": out_days,
        "weekly_summaries": weeks,
        "syllabus_coverage": coverage,
    }
