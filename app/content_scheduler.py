"""
Content-aware day scheduler.

Takes the LMS `syllabus-bundle` payload + the user's mastery state and emits
day cards composed of typed *blocks* (read / watch / practice / mock / recall)
that fit each day's time budget.

This is the engine the new Today / Week screens render. It is intentionally
self-contained — given a bundle dict and a config, it returns plain Python
dicts. No I/O, no DB. The caller is responsible for persistence.

Block shape (matches the contract that web + Flutter both render):

    {
      "block_id":   "<short uuid>",
      "kind":       "read" | "watch" | "practice" | "mock" | "recall",
      "topic_ref":  { "lms_topic_id": "...", "name": "..." },
      "items":      [ { content_kind, content_id, title, est_min, ... } ],
      "rationale":  "...",
      "state":      "pending" | "in_progress" | "completed" | "skipped",
      "completed_at": null
    }

Day shape:

    {
      "day":              "YYYY-MM-DD",
      "phase":            "Foundation" | "Consolidation" | "Revision" | "Final",
      "time_budget_min":  360,
      "blocks":           [...],
      "checkpoint":       { "expected_progress": 0.23, "actual": null }
    }

Mastery dict shape (passed in by the caller):
    { lms_topic_id: { "mastery": 0.0..1.0, "accuracy": 0.0..1.0, "last_studied_days_ago": int } }

If the user has no mastery data yet (cold start), pass {} — the scheduler
treats every topic as 0.0 mastery, which biases the rotation toward P1
high-priority topics first.
"""
from __future__ import annotations
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("planner.scheduler")


# ───────────────────────── tunables ─────────────────────────

# Hard cap on a single day's video minutes — cognitive fatigue heuristic.
MAX_VIDEO_MIN_PER_DAY = 45

# Block ordering preference — read first (build understanding), then watch,
# then practice (test understanding), recall always pinned to top of day.
BLOCK_KIND_ORDER = ["read", "watch", "practice", "mock"]

# How aggressively each phase rotates topics
PHASE_TOPIC_ROTATION_RATE = {
    "Foundation":    1,    # 1 topic per day, deeply
    "Consolidation": 2,    # 2 topics per day, mixed
    "Revision":      3,    # 3 topics per day, fast revisions
    "Final":         4,    # 4 topics per day, mock-heavy
}

# Phase boundaries based on coverage_pct of P1 topics.
PHASE_BOUNDARIES = [
    ("Foundation",    0.00),
    ("Consolidation", 0.45),
    ("Revision",      0.75),
    ("Final",         0.92),
]


# ───────────────────────── helpers ─────────────────────────


def _new_block_id() -> str:
    return uuid.uuid4().hex[:12]


def _phase_for_coverage(p1_coverage: float, days_left: int) -> str:
    """Final phase kicks in unconditionally in last 30 days."""
    if days_left <= 30:
        return "Final"
    name = "Foundation"
    for n, threshold in PHASE_BOUNDARIES:
        if p1_coverage >= threshold:
            name = n
    return name


def _priority_weight(label: Optional[str]) -> float:
    """Map LMS priority labels to a numeric weight for topic ranking."""
    if not label:
        return 1.0
    s = label.upper()
    if "P1" in s or "HIGH" in s:
        return 3.0
    if "P2" in s or "MED" in s:
        return 2.0
    return 1.0


def _topic_score(
    topic: Dict[str, Any],
    mastery: Dict[str, Dict[str, Any]],
    today: date,
) -> float:
    """
    Higher = should be scheduled sooner. Combines:
      - priority label
      - inverse mastery (weak topics ranked higher)
      - recency penalty (haven't touched in a while → bump)
      - coverage gap
    """
    tid = str(topic.get("_id") or topic.get("id"))
    m = mastery.get(tid, {})
    mastery_score = m.get("mastery", 0.0)
    days_idle = m.get("last_studied_days_ago", 999)

    priority = _priority_weight(topic.get("priority_label"))
    weakness_bump = (1.0 - mastery_score) * 2.0
    idle_bump = min(days_idle / 14.0, 1.5)  # cap so a topic untouched for 60d isn't infinitely scored

    # Penalize topics with no available content at all
    counts = topic.get("content_counts", {}) or {}
    has_any = any(counts.get(k, 0) for k in ("notes", "videos", "mcqs", "mocks"))
    if not has_any:
        return -1.0  # never schedule

    return priority + weakness_bump + idle_bump


# ───────────────────────── block builders ─────────────────────────


def _build_read_block(
    topic: Dict[str, Any],
    remaining_min: int,
    used_content_ids: set[str],
    user_multiplier: float,
) -> Optional[Dict[str, Any]]:
    """Pick the smallest unscheduled note that fits."""
    notes = (topic.get("content_ids") or {}).get("notes", []) or []
    notes_meta = (topic.get("notes_meta") or [])  # optional richer payload
    if not notes:
        return None

    # Estimated time per note: prefer per-note metadata, fall back to topic average
    est = topic.get("est_minutes", {}) or {}
    avg_per_note = max(8, int(est.get("read", 22) / max(1, len(notes))))
    avg_per_note = int(avg_per_note * user_multiplier)

    items = []
    spent = 0
    for nid in notes:
        if nid in used_content_ids:
            continue
        if spent + avg_per_note > remaining_min:
            break
        meta = next((m for m in notes_meta if m.get("_id") == nid), {})
        items.append({
            "content_kind": "notes",
            "content_id": nid,
            "title": meta.get("title", "Notes"),
            "est_min": avg_per_note,
        })
        used_content_ids.add(nid)
        spent += avg_per_note
        if len(items) >= 2:  # cap at 2 notes per read block
            break

    if not items:
        return None
    return {
        "block_id": _new_block_id(),
        "kind": "read",
        "topic_ref": {"lms_topic_id": str(topic.get("_id")), "name": topic.get("name", "")},
        "items": items,
        "rationale": "Foundation reading",
        "state": "pending",
        "completed_at": None,
    }


def _build_watch_block(
    topic: Dict[str, Any],
    remaining_min: int,
    day_video_used: int,
    used_content_ids: set[str],
    user_multiplier: float,
) -> Optional[Dict[str, Any]]:
    videos = (topic.get("content_ids") or {}).get("videos", []) or []
    videos_meta = (topic.get("videos_meta") or [])
    if not videos:
        return None
    video_budget = MAX_VIDEO_MIN_PER_DAY - day_video_used
    if video_budget <= 5:
        return None

    est = topic.get("est_minutes", {}) or {}
    avg_per_video = max(6, int(est.get("watch", 20) / max(1, len(videos))))
    avg_per_video = int(avg_per_video * user_multiplier)

    items = []
    spent = 0
    for vid in videos:
        if vid in used_content_ids:
            continue
        if spent + avg_per_video > min(remaining_min, video_budget):
            break
        meta = next((m for m in videos_meta if m.get("_id") == vid), {})
        items.append({
            "content_kind": "video",
            "content_id": vid,
            "title": meta.get("title", "Video"),
            "est_min": avg_per_video,
            "duration_sec": meta.get("duration_sec"),
        })
        used_content_ids.add(vid)
        spent += avg_per_video
        if len(items) >= 2:
            break

    if not items:
        return None
    return {
        "block_id": _new_block_id(),
        "kind": "watch",
        "topic_ref": {"lms_topic_id": str(topic.get("_id")), "name": topic.get("name", "")},
        "items": items,
        "rationale": None,
        "state": "pending",
        "completed_at": None,
    }


def _build_practice_block(
    topic: Dict[str, Any],
    remaining_min: int,
    mastery: Dict[str, Dict[str, Any]],
    user_multiplier: float,
) -> Optional[Dict[str, Any]]:
    counts = topic.get("content_counts") or {}
    available = counts.get("mcqs", 0)
    if available <= 0:
        return None

    tid = str(topic.get("_id"))
    m = mastery.get(tid, {})
    score = m.get("mastery", 0.0)
    # Weak topics → bigger sets, strong topics → small spaced sets.
    if score < 0.4:
        target_count = 50
    elif score < 0.7:
        target_count = 30
    else:
        target_count = 12

    target_count = min(target_count, available)
    per_q_min = 1.5 * user_multiplier
    est_min = int(target_count * per_q_min)
    if est_min > remaining_min:
        # Shrink to fit
        target_count = max(5, int(remaining_min / per_q_min))
        est_min = int(target_count * per_q_min)
        if target_count < 5:
            return None

    target_accuracy = 70 if score < 0.7 else 85

    return {
        "block_id": _new_block_id(),
        "kind": "practice",
        "topic_ref": {"lms_topic_id": tid, "name": topic.get("name", "")},
        "items": [{
            "content_kind": "mcq_set",
            "topic_id": tid,
            "count": target_count,
            "est_min": est_min,
            "target_accuracy": target_accuracy,
        }],
        "rationale": f"accuracy {int(score*100)}%, target {target_accuracy}%",
        "state": "pending",
        "completed_at": None,
    }


def _build_mock_block(
    mock: Dict[str, Any],
    user_multiplier: float,
) -> Dict[str, Any]:
    qcount = mock.get("question_count", 50)
    est_min = int(qcount * 1.2 * user_multiplier + 30)  # +30 review buffer
    return {
        "block_id": _new_block_id(),
        "kind": "mock",
        "topic_ref": {"lms_topic_id": None, "name": mock.get("title", "Mock Exam")},
        "items": [{
            "content_kind": "mock",
            "content_id": str(mock.get("_id")),
            "title": mock.get("title", "Mock Exam"),
            "est_min": est_min,
            "question_count": qcount,
        }],
        "rationale": "Timed mock — analysis runs after submit",
        "state": "pending",
        "completed_at": None,
    }


def _build_recall_block(due_cards: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not due_cards:
        return None
    items = [{
        "content_kind": "recall_card",
        "content_id": str(c.get("id")),
        "title": c.get("topic", "Recall"),
        "est_min": 1,  # ~1 min per card
    } for c in due_cards[:30]]
    return {
        "block_id": _new_block_id(),
        "kind": "recall",
        "topic_ref": {"lms_topic_id": None, "name": "Spaced repetition"},
        "items": items,
        "rationale": f"{len(items)} cards due",
        "state": "pending",
        "completed_at": None,
    }


# ───────────────────────── public API ─────────────────────────


@dataclass
class SchedulerConfig:
    start_date: date
    exam_date: date
    hours_per_day: float = 4.0
    rest_days_per_week: int = 1   # 0..2
    custom_rest_dates: List[date] = field(default_factory=list)
    use_lms_content: bool = True


def flatten_topics(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Walk Cat → Sub → Topic and yield every leaf topic with priority hints."""
    out: List[Dict[str, Any]] = []
    for cat in bundle.get("categories", []) or []:
        for sub in cat.get("subcategories", []) or []:
            for topic in sub.get("topics", []) or []:
                # Inherit priority label from the most specific level that has it.
                label = (
                    topic.get("priority_label")
                    or sub.get("priority_label")
                    or cat.get("priority_label")
                )
                topic = {**topic, "priority_label": label, "_category_name": cat.get("name"), "_subcategory_name": sub.get("name")}
                out.append(topic)
    return out


def collect_mocks(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(bundle.get("mocks_global") or [])


def build_schedule(
    bundle: Dict[str, Any],
    cfg: SchedulerConfig,
    mastery: Optional[Dict[str, Dict[str, Any]]] = None,
    due_recall_cards: Optional[List[Dict[str, Any]]] = None,
    user_multipliers: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Return a list of day dicts from cfg.start_date through cfg.exam_date.
    """
    mastery = mastery or {}
    due_recall_cards = due_recall_cards or []
    user_multipliers = user_multipliers or {"read": 1.0, "watch": 1.0, "mcq": 1.0}

    if not cfg.use_lms_content or not bundle.get("categories"):
        return _build_chapter_only_schedule(cfg)

    topics = flatten_topics(bundle)
    mocks = collect_mocks(bundle)
    # Topic rotation pointer — round-robin so we don't pile the same topic
    # back-to-back across days.
    rotation_idx = 0

    days_total = (cfg.exam_date - cfg.start_date).days + 1
    days: List[Dict[str, Any]] = []

    # Coverage tracker — how many of each topic's content we've already scheduled
    used_content: set[str] = set()

    # Track recall cards we've placed so we don't dump every due card on day 1.
    recall_pool = list(due_recall_cards)

    for day_offset in range(days_total):
        d = cfg.start_date + timedelta(days=day_offset)
        days_left = (cfg.exam_date - d).days

        # Rest day handling
        if d in cfg.custom_rest_dates or _is_weekly_rest_day(d, cfg.rest_days_per_week):
            days.append({
                "day": d.isoformat(),
                "phase": "Rest",
                "time_budget_min": 0,
                "blocks": [],
                "checkpoint": {"expected_progress": None, "actual": None},
            })
            continue

        # Compute current phase from coverage
        p1_coverage = _compute_p1_coverage(topics, used_content)
        phase = _phase_for_coverage(p1_coverage, days_left)

        budget = int(cfg.hours_per_day * 60)
        remaining = budget
        day_video_used = 0
        blocks: List[Dict[str, Any]] = []

        # 1) Recall first — always pin a small recall block at the top
        chunk = recall_pool[:8]
        recall_pool = recall_pool[8:]
        recall_block = _build_recall_block(chunk)
        if recall_block:
            blocks.append(recall_block)
            spent = sum(i["est_min"] for i in recall_block["items"])
            remaining -= spent

        # 2) Mock days — Final phase gets one mock every 3 days (if available)
        if phase == "Final" and mocks and day_offset % 3 == 0:
            mock = mocks[day_offset // 3 % len(mocks)]
            mock_block = _build_mock_block(mock, user_multipliers["mcq"])
            blocks.append(mock_block)
            remaining -= mock_block["items"][0]["est_min"]

        # 3) Topic blocks — pick top-N topics for today by score
        rotation_count = PHASE_TOPIC_ROTATION_RATE[phase]
        ranked = sorted(topics, key=lambda t: -_topic_score(t, mastery, d))
        # Round-robin offset so days don't all see the same top-3
        picked: List[Dict[str, Any]] = []
        for i in range(rotation_count):
            if not ranked:
                break
            picked.append(ranked[(rotation_idx + i) % len(ranked)])
        rotation_idx = (rotation_idx + rotation_count) % max(1, len(ranked))

        # 4) For each picked topic, fill the day greedily
        for topic in picked:
            if remaining < 15:
                break
            for kind in BLOCK_KIND_ORDER:
                if remaining < 10:
                    break
                if kind == "mock":
                    continue  # mocks handled above
                builder = {
                    "read": lambda: _build_read_block(topic, remaining, used_content, user_multipliers["read"]),
                    "watch": lambda: _build_watch_block(topic, remaining, day_video_used, used_content, user_multipliers["watch"]),
                    "practice": lambda: _build_practice_block(topic, remaining, mastery, user_multipliers["mcq"]),
                }[kind]()
                if not builder:
                    continue
                blocks.append(builder)
                spent = sum(i["est_min"] for i in builder["items"])
                remaining -= spent
                if kind == "watch":
                    day_video_used += spent

        days.append({
            "day": d.isoformat(),
            "phase": phase,
            "time_budget_min": budget,
            "blocks": blocks,
            "checkpoint": {
                "expected_progress": round((day_offset + 1) / days_total, 3),
                "actual": None,
            },
        })

    return days


def _is_weekly_rest_day(d: date, rest_days_per_week: int) -> bool:
    """Sunday is the default first rest day; Saturday is the second."""
    if rest_days_per_week >= 1 and d.weekday() == 6:
        return True
    if rest_days_per_week >= 2 and d.weekday() == 5:
        return True
    return False


def _compute_p1_coverage(topics: List[Dict[str, Any]], used_content: set[str]) -> float:
    """Fraction of P1 topic content_ids that have been scheduled at least once."""
    p1_total = 0
    p1_seen = 0
    for t in topics:
        if _priority_weight(t.get("priority_label")) < 3.0:
            continue
        ids = t.get("content_ids") or {}
        all_ids = (ids.get("notes") or []) + (ids.get("videos") or [])
        if not all_ids:
            continue
        p1_total += len(all_ids)
        p1_seen += sum(1 for cid in all_ids if cid in used_content)
    if p1_total == 0:
        return 0.0
    return p1_seen / p1_total


def _build_chapter_only_schedule(cfg: SchedulerConfig) -> List[Dict[str, Any]]:
    """
    Fallback when use_lms_content=False or bundle is empty (free user, no
    subscriptions). The day card still has structure but blocks are
    chapter-reference shells with no specific content_ids — the existing
    legacy planner.py builders fill these in via SYLLABUS_TREE.
    """
    days = []
    d = cfg.start_date
    while d <= cfg.exam_date:
        days.append({
            "day": d.isoformat(),
            "phase": "Chapter-mode",
            "time_budget_min": int(cfg.hours_per_day * 60),
            "blocks": [],   # legacy planner fills this
            "checkpoint": {"expected_progress": None, "actual": None},
            "_chapter_only": True,
        })
        d += timedelta(days=1)
    return days
