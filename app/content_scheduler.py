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
      "minutes":    <int>,   # SUM of items' est_min — the canonical duration the UI reads
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
# Raised from 45 to 90 now that we use real durations from the LMS; a
# single long surgery video can be 30-40 min, and students need 2-3/day.
MAX_VIDEO_MIN_PER_DAY = 90

# Block ordering preference — read first (build understanding), then watch,
# then practice (test understanding), recall always pinned to top of day.
BLOCK_KIND_ORDER = ["read", "watch", "practice", "mock"]

# How aggressively each phase rotates topics
# Upgrade: previous Foundation=1 meant a 50-topic syllabus only cycled once
# every 50 days. Dense rotation gives users a feeling of forward motion and
# spreads content-ids across more days so the round-robin pointer actually
# covers the P1 tier inside the phase window.
PHASE_TOPIC_ROTATION_RATE = {
    "Foundation":    3,    # 1 deep + 2 light touches
    "Consolidation": 4,
    "Revision":      5,
    "Final":         4,    # mock-heavy — mocks counted separately
}

# Minimum attempts below which a user is treated as "cold start" — bumps
# them into the diagnostic-week path in build_schedule().
COLD_START_MCQ_THRESHOLD = 50
DIAGNOSTIC_DAYS = 5
DIAGNOSTIC_MINI_MOCK_Q = 20

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


def _finalize_block(block: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Stamp the canonical `minutes` total on a block from its items' est_min.

    This is the single source of truth the dashboard / today card / analytics
    read from. Builders used to omit this field and callers had to re-derive
    it from `items`, which led to silent `0 minutes` bugs in planner_v2.
    """
    if not block:
        return block
    items = block.get("items") or []
    block["minutes"] = int(sum(int(i.get("est_min") or 0) for i in items))
    return block


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
    """Map LMS priority labels to a numeric weight for topic ordering.

    Higher weight = scheduled earlier and gets more daily time share.
    Uses the 5-level Priority Manager labels from the LMS backend.
    """
    WEIGHTS = {
        "Must Know":     5.0,
        "High Yield":    4.0,
        "Important":     3.0,
        "Good to Know":  2.0,
        "Low Priority":  1.0,
    }
    if not label:
        return 2.0  # untagged content treated as "Good to Know"
    # Exact match
    w = WEIGHTS.get(label.strip())
    if w is not None:
        return w
    # Legacy fallback
    up = label.upper()
    if "MUST" in up:
        return 5.0
    if "HIGH" in up or "P1" in up:
        return 4.0
    if "IMPORTANT" in up:
        return 3.0
    if "GOOD" in up or "P2" in up or "MID" in up:
        return 2.0
    if "LOW" in up or "P3" in up:
        return 1.0
    return 2.0


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
    m = mastery.get(tid) or {}
    # Defensive: the LMS signal can send nulls when a topic has never been touched.
    mastery_score = float(m.get("mastery") or 0.0)
    days_idle_raw = m.get("last_studied_days_ago")

    priority = _priority_weight(topic.get("priority_label"))
    weakness_bump = (1.0 - mastery_score) * 2.0
    # Cold-start fix: a topic the user has NEVER touched is not "999 days stale"
    # (which would pin idle_bump to its cap for every cold topic and collapse
    # the entire ranking back onto priority_label ties). Treat unknown idleness
    # as a neutral 0.4 bump so priority + weakness can still differentiate.
    if days_idle_raw is None:
        idle_bump = 0.4
    else:
        days_idle = float(days_idle_raw)
        idle_bump = min(days_idle / 14.0, 1.5)

    # Penalize topics with no available content at all
    counts = topic.get("content_counts", {}) or {}
    has_any = any(counts.get(k, 0) for k in ("notes", "videos", "mcqs", "mocks"))
    if not has_any:
        return -1.0  # never schedule

    return priority + weakness_bump + idle_bump


# ───────────────────────── block builders ─────────────────────────


def _estimate_note_minutes(meta: Dict[str, Any], fallback: int = 22) -> int:
    """Derive reading time from the note's real page count.

    Heuristic: ~2.5 minutes per page (dense medical text with diagrams).
    The LMS provides `total_pages` in notes_meta; when missing we fall
    back to the topic-level average.
    """
    pages = meta.get("total_pages") or meta.get("totalPage")
    if pages and int(pages) > 0:
        return max(5, int(int(pages) * 2.5))
    return max(5, fallback)


def _build_read_block(
    topic: Dict[str, Any],
    remaining_min: int,
    used_content_ids: set[str],
    user_multiplier: float,
) -> Optional[Dict[str, Any]]:
    """Pick the smallest unscheduled note that fits, using real page counts."""
    notes = (topic.get("content_ids") or {}).get("notes", []) or []
    notes_meta = (topic.get("notes_meta") or [])
    if not notes:
        return None

    # Build a quick-lookup for per-note metadata (page counts, titles).
    meta_by_id: Dict[str, Dict[str, Any]] = {}
    for m in notes_meta:
        mid = m.get("_id") or ""
        if mid:
            meta_by_id[str(mid)] = m

    # Topic-level fallback: if no individual page counts exist, use
    # est_minutes.read / n_notes as the per-note average.
    est = topic.get("est_minutes", {}) or {}
    topic_avg = max(8, int((est.get("read") or 22) / max(1, len(notes))))

    items = []
    spent = 0
    for nid in notes:
        if nid in used_content_ids:
            continue
        meta = meta_by_id.get(str(nid), {})
        raw_min = _estimate_note_minutes(meta, fallback=topic_avg)
        note_min = max(5, int(raw_min * user_multiplier))
        if spent + note_min > remaining_min:
            break
        items.append({
            "content_kind": "notes",
            "content_id": nid,
            "title": meta.get("title", "Notes"),
            "total_pages": meta.get("total_pages") or meta.get("totalPage"),
            "est_min": note_min,
        })
        used_content_ids.add(nid)
        spent += note_min
        if len(items) >= 3:  # cap at 3 notes per read block
            break

    if not items:
        return None
    return _finalize_block({
        "block_id": _new_block_id(),
        "kind": "read",
        "topic_ref": {"lms_topic_id": str(topic.get("_id")), "name": topic.get("name", "")},
        "items": items,
        "rationale": "Foundation reading",
        "state": "pending",
        "completed_at": None,
    })


def _estimate_video_minutes(meta: Dict[str, Any], fallback: int = 20) -> int:
    """Derive watch time from the video's real duration_sec.

    The LMS stores actual duration in seconds on each video document.
    We round up to whole minutes and add a 10% buffer for pausing /
    note-taking. When duration_sec is missing, fall back to topic avg.
    """
    dur = meta.get("duration_sec") or meta.get("duration")
    if dur and float(dur) > 0:
        return max(3, int(float(dur) / 60 * 1.1 + 0.5))  # +10% buffer, round up
    return max(3, fallback)


def _build_watch_block(
    topic: Dict[str, Any],
    remaining_min: int,
    day_video_used: int,
    used_content_ids: set[str],
    user_multiplier: float,
) -> Optional[Dict[str, Any]]:
    """Schedule unwatched videos using real durations from LMS metadata."""
    videos = (topic.get("content_ids") or {}).get("videos", []) or []
    videos_meta = (topic.get("videos_meta") or [])
    if not videos:
        return None
    video_budget = MAX_VIDEO_MIN_PER_DAY - day_video_used
    if video_budget <= 5:
        return None

    # Build quick-lookup for per-video metadata (duration, title).
    meta_by_id: Dict[str, Dict[str, Any]] = {}
    for m in videos_meta:
        mid = m.get("_id") or ""
        if mid:
            meta_by_id[str(mid)] = m

    # Topic-level fallback when individual durations are missing.
    est = topic.get("est_minutes", {}) or {}
    topic_avg = max(6, int((est.get("watch") or 20) / max(1, len(videos))))

    items = []
    spent = 0
    for vid in videos:
        if vid in used_content_ids:
            continue
        meta = meta_by_id.get(str(vid), {})
        raw_min = _estimate_video_minutes(meta, fallback=topic_avg)
        vid_min = max(3, int(raw_min * user_multiplier))
        if spent + vid_min > min(remaining_min, video_budget):
            break
        items.append({
            "content_kind": "video",
            "content_id": vid,
            "title": meta.get("title", "Video"),
            "est_min": vid_min,
            "duration_sec": meta.get("duration_sec") or meta.get("duration"),
        })
        used_content_ids.add(vid)
        spent += vid_min
        if len(items) >= 3:  # allow up to 3 shorter videos per block
            break

    if not items:
        return None
    return _finalize_block({
        "block_id": _new_block_id(),
        "kind": "watch",
        "topic_ref": {"lms_topic_id": str(topic.get("_id")), "name": topic.get("name", "")},
        "items": items,
        "rationale": None,
        "state": "pending",
        "completed_at": None,
    })


def _build_practice_block(
    topic: Dict[str, Any],
    remaining_min: int,
    mastery: Dict[str, Dict[str, Any]],
    user_multiplier: float,
) -> Optional[Dict[str, Any]]:
    counts = topic.get("content_counts") or {}
    available = int(counts.get("mcqs") or 0)
    if available <= 0:
        return None

    tid = str(topic.get("_id"))
    m = mastery.get(tid) or {}
    score = float(m.get("mastery") or 0.0)
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

    return _finalize_block({
        "block_id": _new_block_id(),
        "kind": "practice",
        "topic_ref": {"lms_topic_id": tid, "name": topic.get("name", "")},
        "items": [{
            "content_kind": "mcq_set",
            # `content_id` is the canonical identifier dashboards count unique
            # sets by. For a live MCQ batch there's no single doc id, so we
            # synthesise a stable per-topic-per-day id the uniqueness counter
            # can use while still carrying topic_id for the LMS fetcher.
            "content_id": f"mcq:{tid}",
            "topic_id": tid,
            "count": target_count,
            "est_min": est_min,
            "target_accuracy": target_accuracy,
        }],
        "rationale": f"accuracy {int(score*100)}%, target {target_accuracy}%",
        "state": "pending",
        "completed_at": None,
    })


def _build_mock_block(
    mock: Dict[str, Any],
    user_multiplier: float,
) -> Dict[str, Any]:
    qcount = mock.get("question_count", 50)
    est_min = int(qcount * 1.2 * user_multiplier + 30)  # +30 review buffer
    return _finalize_block({
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
    })


def _build_mini_mock_block(
    topic: Dict[str, Any],
    n_questions: int,
    user_multiplier: float,
) -> Dict[str, Any]:
    """Diagnostic-week mini mock: scoped to a single topic for baseline measurement."""
    tid = str(topic.get("_id") or "")
    est_min = int(n_questions * 1.2 * user_multiplier + 5)
    return _finalize_block({
        "block_id": _new_block_id(),
        "kind": "mock",
        "topic_ref": {"lms_topic_id": tid, "name": topic.get("name", "")},
        "items": [{
            "content_kind": "mini_mock",
            "content_id": f"diag:{tid}",
            "topic_id": tid,
            "title": f"Diagnostic mini mock — {topic.get('name', '')}",
            "est_min": est_min,
            "question_count": n_questions,
        }],
        "rationale": "Baseline diagnostic — sets up mastery scoring for this topic",
        "state": "pending",
        "completed_at": None,
    })


def _build_recall_block(due_cards: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not due_cards:
        return None
    items = [{
        "content_kind": "recall_card",
        "content_id": str(c.get("id")),
        "title": c.get("topic", "Recall"),
        "est_min": 1,  # ~1 min per card
    } for c in due_cards[:30]]
    return _finalize_block({
        "block_id": _new_block_id(),
        "kind": "recall",
        "topic_ref": {"lms_topic_id": None, "name": "Spaced repetition"},
        "items": items,
        "rationale": f"{len(items)} cards due",
        "state": "pending",
        "completed_at": None,
    })


# ───────────────────────── public API ─────────────────────────


@dataclass
class SchedulerConfig:
    start_date: date
    # end_date is the canonical plan window terminus (was `exam_date` in the
    # old API; we keep `exam_date` as an alias property below for back-compat
    # with any callers still passing it as a kwarg).
    end_date: date
    hours_per_day: float = 4.0
    rest_days_per_week: int = 1   # 0..2
    custom_rest_dates: List[date] = field(default_factory=list)
    use_lms_content: bool = True
    # New fields driven by the rich generator form on v2.html.
    daily_minutes: Optional[int] = None      # explicit override; falls back to hours_per_day*60
    mocks_count: Optional[int] = None        # cap on total mocks across the plan
    min_per_mcq: float = 1.5                 # MCQ pacing knob
    revision_rounds: int = 1                 # 1..3 — how many full passes through P1 in Revision/Final
    focus_topic_ids: List[str] = field(default_factory=list)  # user-prioritised topic ids

    @property
    def exam_date(self) -> date:
        """Back-compat alias — older callers and tests still reference exam_date."""
        return self.end_date

    @exam_date.setter
    def exam_date(self, value: date) -> None:
        self.end_date = value

    @property
    def effective_daily_minutes(self) -> int:
        if self.daily_minutes is not None and self.daily_minutes > 0:
            return int(self.daily_minutes)
        return int(self.hours_per_day * 60)


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


# ═══════════════════════════════════════════════════════════════
#  Staged Content Ladder — the study flow per topic
# ═══════════════════════════════════════════════════════════════
#
# Each topic progresses through 5 stages with expanding spacing
# for spaced-repetition retention:
#
#   Stage 1: READ notes            → pages * 3 min/page
#   Stage 2: WATCH videos          → duration_sec / 60 * 1.5
#   Stage 3: MCQ PRACTICE          → count * 1.5 min  (untimed practice)
#   Stage 4: TEST + DISCUSSION     → count * 5 min    (exam mode, review)
#   Stage 5: MOCK                  → full day
#
# On any given day, the student works 3-4 topics at DIFFERENT stages,
# so they always mix fresh learning + reinforcement + testing.

STAGES = ["read", "watch", "practice", "test", "mock"]

# ── Priority levels from the LMS Priority Manager ──
#
#   Must Know     → highest urgency, tightest spacing, most MCQs
#   High Yield    → core exam topics, tight spacing
#   Important     → solid coverage needed, moderate spacing
#   Good to Know  → breadth topics, wider spacing
#   Low Priority  → skim only if time allows, widest spacing
#
# Spacing = study-day gaps between stages (read → watch → practice → test)
SPACING_BY_PRIORITY = {
    "Must Know":     [0, 1, 3,  5,  8],
    "High Yield":    [0, 2, 4,  7, 10],
    "Important":     [0, 3, 5,  9, 14],
    "Good to Know":  [0, 4, 7, 12, 20],
    "Low Priority":  [0, 5, 10, 18, 28],
}

# How many MCQs per practice/test session by priority
MCQ_COUNTS = {
    "Must Know":     {"practice": 50, "test": 40},
    "High Yield":    {"practice": 40, "test": 30},
    "Important":     {"practice": 30, "test": 20},
    "Good to Know":  {"practice": 20, "test": 15},
    "Low Priority":  {"practice": 10, "test": 10},
}

# Default for topics with no priority assigned
_DEFAULT_PRIORITY = "Important"


def _get_priority_key(label: Optional[str]) -> str:
    """Map an LMS priority_label string to our spacing/MCQ lookup key.

    The LMS Priority Manager assigns one of 5 labels:
      Must Know, High Yield, Important, Good to Know, Low Priority.
    The label string arrives as-is in the bundle's `priority_label` field.
    Legacy P1/P2/P3 labels from the old planner are also handled.
    """
    if not label:
        return _DEFAULT_PRIORITY
    s = label.strip()
    # Exact match first (the normal path)
    if s in SPACING_BY_PRIORITY:
        return s
    # Legacy / fallback mapping
    up = s.upper()
    if "MUST" in up:
        return "Must Know"
    if "HIGH" in up or "P1" in up:
        return "High Yield"
    if "IMPORTANT" in up:
        return "Important"
    if "GOOD" in up or "P2" in up or "MID" in up:
        return "Good to Know"
    if "LOW" in up or "P3" in up:
        return "Low Priority"
    return _DEFAULT_PRIORITY


def _estimate_topic_stage_minutes(
    topic: Dict[str, Any],
    stage: str,
    priority_key: str,
    user_multipliers: Dict[str, float],
) -> int:
    """Estimate how many minutes a stage will take for this topic."""
    mul = user_multipliers or {"read": 1.0, "watch": 1.0, "mcq": 1.0}

    if stage == "read":
        notes_meta = topic.get("notes_meta") or []
        if not notes_meta:
            counts = topic.get("content_counts") or {}
            n = int(counts.get("notes") or 0)
            return int(n * 22 * mul.get("read", 1.0)) if n else 0
        total = 0
        for m in notes_meta:
            total += _estimate_note_minutes(m, fallback=22)
        return max(0, int(total * mul.get("read", 1.0)))

    if stage == "watch":
        videos_meta = topic.get("videos_meta") or []
        if not videos_meta:
            counts = topic.get("content_counts") or {}
            n = int(counts.get("videos") or 0)
            return int(n * 20 * mul.get("watch", 1.0)) if n else 0
        total = 0
        for m in videos_meta:
            total += _estimate_video_minutes(m, fallback=20)
        return max(0, int(total * mul.get("watch", 1.0)))

    if stage == "practice":
        counts = topic.get("content_counts") or {}
        available = int(counts.get("mcqs") or 0)
        target = min(available, MCQ_COUNTS[priority_key]["practice"])
        return int(target * 1.5 * mul.get("mcq", 1.0)) if target else 0

    if stage == "test":
        counts = topic.get("content_counts") or {}
        available = int(counts.get("mcqs") or 0)
        target = min(available, MCQ_COUNTS[priority_key]["test"])
        return int(target * 5.0 * mul.get("mcq", 1.0)) if target else 0

    return 0  # mock is handled separately


def _build_stage_block(
    topic: Dict[str, Any],
    stage: str,
    priority_key: str,
    remaining_min: int,
    used_content_ids: set,
    user_multipliers: Dict[str, float],
    mastery: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Build the block for a specific stage of the content ladder."""
    tid = str(topic.get("_id") or "")
    mul = user_multipliers or {"read": 1.0, "watch": 1.0, "mcq": 1.0}

    if stage == "read":
        return _build_read_block(topic, remaining_min, used_content_ids, mul.get("read", 1.0))

    if stage == "watch":
        return _build_watch_block(topic, remaining_min, 0, used_content_ids, mul.get("watch", 1.0))

    if stage == "practice":
        # Override the mastery-based count with our priority-driven count
        counts = topic.get("content_counts") or {}
        available = int(counts.get("mcqs") or 0)
        if available <= 0:
            return None
        target = min(available, MCQ_COUNTS[priority_key]["practice"])
        per_q = 1.5 * mul.get("mcq", 1.0)
        est_min = int(target * per_q)
        if est_min > remaining_min:
            target = max(5, int(remaining_min / per_q))
            est_min = int(target * per_q)
            if target < 5:
                return None
        return _finalize_block({
            "block_id": _new_block_id(),
            "kind": "practice",
            "topic_ref": {"lms_topic_id": tid, "name": topic.get("name", "")},
            "items": [{
                "content_kind": "mcq_set",
                "content_id": f"mcq:{tid}",
                "topic_id": tid,
                "count": target,
                "est_min": est_min,
                "target_accuracy": 70,
                "mode": "practice",
            }],
            "rationale": f"Practice mode — {target} MCQs at 1.5 min each",
            "state": "pending",
            "completed_at": None,
        })

    if stage == "test":
        counts = topic.get("content_counts") or {}
        available = int(counts.get("mcqs") or 0)
        if available <= 0:
            return None
        target = min(available, MCQ_COUNTS[priority_key]["test"])
        per_q = 5.0 * mul.get("mcq", 1.0)
        est_min = int(target * per_q)
        if est_min > remaining_min:
            target = max(5, int(remaining_min / per_q))
            est_min = int(target * per_q)
            if target < 5:
                return None
        return _finalize_block({
            "block_id": _new_block_id(),
            "kind": "practice",
            "topic_ref": {"lms_topic_id": tid, "name": topic.get("name", "")},
            "items": [{
                "content_kind": "mcq_set",
                "content_id": f"test:{tid}",
                "topic_id": tid,
                "count": target,
                "est_min": est_min,
                "target_accuracy": 85,
                "mode": "test",
            }],
            "rationale": f"Test + discussion mode — {target} MCQs at 5 min each (exam pace)",
            "state": "pending",
            "completed_at": None,
        })

    return None


def build_schedule(
    bundle: Dict[str, Any],
    cfg: SchedulerConfig,
    mastery: Optional[Dict[str, Dict[str, Any]]] = None,
    due_recall_cards: Optional[List[Dict[str, Any]]] = None,
    user_multipliers: Optional[Dict[str, float]] = None,
    plan_shape: Optional[Dict[str, Any]] = None,
    total_mcq_attempts: int = 0,
) -> List[Dict[str, Any]]:
    """
    Staged content ladder scheduler.

    Each topic progresses through: read notes → watch videos → MCQ practice
    → test + discussion → mock, with expanding spacing between stages for
    spaced-repetition retention.

    On any given day, the student works on 3-4 topics at DIFFERENT stages
    so they always mix fresh learning + reinforcement + testing.

    Mock exams occupy a full day with no other content.
    """
    mastery = mastery or {}
    due_recall_cards = due_recall_cards or []
    user_multipliers = user_multipliers or {"read": 1.0, "watch": 1.0, "mcq": 1.0}

    if not cfg.use_lms_content or not bundle.get("categories"):
        return _build_chapter_only_schedule(cfg, user_multipliers)

    topics = flatten_topics(bundle)
    topics_by_id = {str(t.get("_id") or ""): t for t in topics}
    mocks = collect_mocks(bundle)

    # ── 1. Order topics by priority + weakness ──
    shape = plan_shape or {}
    shape_order: List[str] = list(shape.get("ordered_topic_ids") or [])
    if shape_order:
        ordered_topics: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for tid in shape_order:
            t = topics_by_id.get(str(tid))
            if t is not None and str(tid) not in seen:
                ordered_topics.append(t)
                seen.add(str(tid))
        tail = sorted(
            (t for t in topics if str(t.get("_id") or "") not in seen),
            key=lambda t: -_topic_score(t, mastery, cfg.start_date),
        )
        ordered_topics.extend(tail)
    else:
        ordered_topics = sorted(topics, key=lambda t: -_topic_score(t, mastery, cfg.start_date))

    # Filter to topics that have any content at all
    ordered_topics = [
        t for t in ordered_topics
        if any((t.get("content_counts") or {}).get(k, 0)
               for k in ("notes", "videos", "mcqs"))
    ]

    # ── 2. Build study day calendar (excluding rest days + mock days) ──
    days_total = (cfg.end_date - cfg.start_date).days + 1
    all_dates: List[date] = []
    rest_dates: set[date] = set()
    for day_offset in range(days_total):
        d = cfg.start_date + timedelta(days=day_offset)
        if d in cfg.custom_rest_dates or _is_weekly_rest_day(d, cfg.rest_days_per_week):
            rest_dates.add(d)
        else:
            all_dates.append(d)

    # Reserve mock days: evenly distributed across the last 25% of study days.
    # Each mock gets a full day — no other content.
    mock_count = min(len(mocks), cfg.mocks_count or len(mocks))
    mock_day_indices: set[int] = set()
    mock_day_assignments: Dict[int, Dict[str, Any]] = {}  # study_day_idx → mock dict
    if mock_count > 0 and len(all_dates) > mock_count:
        # Place mocks in the back quarter of the plan
        quarter_start = int(len(all_dates) * 0.75)
        available_for_mocks = len(all_dates) - quarter_start
        if available_for_mocks < mock_count:
            quarter_start = max(0, len(all_dates) - mock_count - 2)
            available_for_mocks = len(all_dates) - quarter_start
        interval = max(1, available_for_mocks // (mock_count + 1))
        for mi in range(mock_count):
            idx = quarter_start + (mi + 1) * interval
            idx = min(idx, len(all_dates) - 1)
            mock_day_indices.add(idx)
            mock_day_assignments[idx] = mocks[mi % len(mocks)]

    # Study days = all_dates minus mock days
    study_days: List[date] = []
    study_day_to_idx: Dict[date, int] = {}  # date → study_day_number (0-based)
    for i, d in enumerate(all_dates):
        if i not in mock_day_indices:
            study_day_to_idx[d] = len(study_days)
            study_days.append(d)

    n_study_days = len(study_days)
    if n_study_days == 0:
        return []

    # ── 3. Pre-compute the content ladder ──
    # For each topic, assign a study-day offset for each stage.
    # Topics start staggered: ~2 new topics begin per study day so the
    # calendar fills with a mix of stages.
    #
    # Entry: (study_day_offset, topic_dict, stage_name, priority_key)
    ladder_entries: List[tuple[int, Dict[str, Any], str, str]] = []

    # How many new topics to seed per study day — controls density.
    # Aim to start all topics within the first ~50-60% of study days so
    # stages 3-4 have room to land before the plan ends.
    seed_window = max(1, int(n_study_days * 0.55))
    topics_per_day = max(1, len(ordered_topics) / seed_window)
    # Track fractional accumulation for clean staggering.
    accum = 0.0

    for topic_idx, topic in enumerate(ordered_topics):
        pk = _get_priority_key(topic.get("priority_label"))
        spacing = SPACING_BY_PRIORITY[pk]

        # Start day for this topic (staggered)
        start_study_day = min(int(accum), n_study_days - 1)
        accum += 1.0 / topics_per_day if topics_per_day else 1

        # Skip stages with no content. A topic with 0 notes skips "read",
        # a topic with 0 videos skips "watch", etc.
        counts = topic.get("content_counts") or {}
        has_notes = int(counts.get("notes") or 0) > 0
        has_videos = int(counts.get("videos") or 0) > 0
        has_mcqs = int(counts.get("mcqs") or 0) > 0

        stage_list = []
        if has_notes:
            stage_list.append("read")
        if has_videos:
            stage_list.append("watch")
        if has_mcqs:
            stage_list.append("practice")
        if has_mcqs:
            stage_list.append("test")
        # Mock stage is handled globally, not per-topic

        cumulative_offset = start_study_day
        for si, stage in enumerate(stage_list):
            # Find the matching spacing index — map sequential available
            # stages to the spacing array positions
            stage_idx = STAGES.index(stage) if stage in STAGES else si
            gap = spacing[min(stage_idx, len(spacing) - 1)]
            if si == 0:
                target_day = start_study_day
            else:
                target_day = cumulative_offset + gap

            # Clamp to plan window
            target_day = min(target_day, n_study_days - 1)
            ladder_entries.append((target_day, topic, stage, pk))
            cumulative_offset = target_day

    # ── 4. Bin ladder entries by study-day offset ──
    entries_by_day: Dict[int, List[tuple[Dict[str, Any], str, str]]] = {}
    for sd_offset, topic, stage, pk in ladder_entries:
        entries_by_day.setdefault(sd_offset, []).append((topic, stage, pk))

    # ── 5. Build day cards ──
    recall_pool = list(due_recall_cards)
    used_content: set[str] = set()

    # Build a lookup from date → day_offset (0-based from start_date)
    date_to_offset: Dict[date, int] = {}
    for i in range(days_total):
        d = cfg.start_date + timedelta(days=i)
        date_to_offset[d] = i

    days: List[Dict[str, Any]] = []
    study_day_counter = 0

    for day_offset in range(days_total):
        d = cfg.start_date + timedelta(days=day_offset)

        # Rest day
        if d in rest_dates:
            days.append({
                "day": d.isoformat(),
                "phase": "Rest",
                "time_budget_min": 0,
                "blocks": [],
                "checkpoint": {"expected_progress": None, "actual": None},
            })
            continue

        # Find the all_dates index for this date
        all_dates_idx = None
        for ai, ad in enumerate(all_dates):
            if ad == d:
                all_dates_idx = ai
                break

        # Mock day — full day, no other content
        if all_dates_idx is not None and all_dates_idx in mock_day_indices:
            mock_dict = mock_day_assignments[all_dates_idx]
            mock_block = _build_mock_block(mock_dict, user_multipliers.get("mcq", 1.0))
            days.append({
                "day": d.isoformat(),
                "phase": "Mock Day",
                "time_budget_min": cfg.effective_daily_minutes,
                "blocks": [mock_block],
                "checkpoint": {
                    "expected_progress": round((day_offset + 1) / days_total, 3),
                    "actual": None,
                },
            })
            continue

        # Regular study day
        budget = cfg.effective_daily_minutes
        remaining = budget
        blocks: List[Dict[str, Any]] = []

        # Determine phase label from progress through the plan
        progress_pct = study_day_counter / max(1, n_study_days)
        if progress_pct < 0.40:
            phase = "Foundation"
        elif progress_pct < 0.65:
            phase = "Consolidation"
        elif progress_pct < 0.85:
            phase = "Revision"
        else:
            phase = "Final"

        # a) Recall block — pinned at top
        chunk = recall_pool[:8]
        recall_pool = recall_pool[8:]
        recall_block = _build_recall_block(chunk)
        if recall_block:
            blocks.append(recall_block)
            remaining -= int(recall_block.get("minutes") or 0)

        # b) Fill from the ladder entries scheduled for this study day
        day_entries = entries_by_day.get(study_day_counter, [])

        # Sort: P1 first, then by stage order (read before watch before practice)
        stage_order = {"read": 0, "watch": 1, "practice": 2, "test": 3}
        priority_order = {"P1_HIGH": 0, "P2_MID": 1, "P3_LOW": 2}
        day_entries.sort(key=lambda e: (priority_order.get(e[2], 1), stage_order.get(e[1], 9)))

        for topic, stage, pk in day_entries:
            if remaining < 10:
                break
            block = _build_stage_block(
                topic, stage, pk, remaining,
                used_content, user_multipliers, mastery,
            )
            if block:
                blocks.append(block)
                remaining -= int(block.get("minutes") or 0)

        # c) If the day still has budget left (>30 min), fill with extra
        #    practice blocks from the weakest topics that have MCQs.
        if remaining > 30:
            for topic in ordered_topics:
                if remaining < 20:
                    break
                tid = str(topic.get("_id") or "")
                counts = topic.get("content_counts") or {}
                if int(counts.get("mcqs") or 0) <= 0:
                    continue
                pk = _get_priority_key(topic.get("priority_label"))
                extra = _build_stage_block(
                    topic, "practice", pk, remaining,
                    used_content, user_multipliers, mastery,
                )
                if extra:
                    blocks.append(extra)
                    remaining -= int(extra.get("minutes") or 0)
                    break  # one extra block is enough

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
        study_day_counter += 1

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


def _build_chapter_only_schedule(
    cfg: SchedulerConfig,
    user_multipliers: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Fallback when use_lms_content=False or bundle is empty (free user, no
    subscription). Populates real read / practice blocks referencing the
    hardcoded SYLLABUS_TREE from app/priorities.py so the day cards are
    never empty. MCQ sets here are generic (no content_id) and rendered by
    the client as "open the practice bank" links.
    """
    user_multipliers = user_multipliers or {"read": 1.0, "watch": 1.0, "mcq": 1.0}
    try:
        from app.priorities import SYLLABUS_TREE, get_subtopics  # noqa: F401
    except Exception:
        SYLLABUS_TREE = {}

    # Flatten the tree into (topic_name, subtopic_dict, priority_weight).
    flat: List[tuple[str, Dict[str, Any], float]] = []
    for topic_name, meta in (SYLLABUS_TREE or {}).items():
        pw = _priority_weight(meta.get("priority") or "P2_MID")
        for sub in meta.get("subtopics") or []:
            flat.append((topic_name, sub, pw))
    # Higher priority first.
    flat.sort(key=lambda x: (-x[2], x[0]))

    read_min_per_sub = int(25 * user_multipliers["read"])
    practice_min_per_set = int(25 * user_multipliers["mcq"])

    days: List[Dict[str, Any]] = []
    total = (cfg.end_date - cfg.start_date).days + 1
    cursor = 0

    for day_offset in range(total):
        d = cfg.start_date + timedelta(days=day_offset)
        if d in cfg.custom_rest_dates or _is_weekly_rest_day(d, cfg.rest_days_per_week):
            days.append({
                "day": d.isoformat(),
                "phase": "Rest",
                "time_budget_min": 0,
                "blocks": [],
                "checkpoint": {"expected_progress": None, "actual": None},
            })
            continue

        budget = cfg.effective_daily_minutes
        remaining = budget
        blocks: List[Dict[str, Any]] = []

        # 2 read blocks + 1 practice block per day, cycling through the flat list.
        for _ in range(2):
            if not flat or remaining < read_min_per_sub + 5:
                break
            topic_name, sub, _pw = flat[cursor % len(flat)]
            cursor += 1
            sub_title = sub.get("name") if isinstance(sub, dict) else str(sub)
            ref = sub.get("ref") if isinstance(sub, dict) else None
            block = _finalize_block({
                "block_id": _new_block_id(),
                "kind": "read",
                "topic_ref": {"lms_topic_id": None, "name": topic_name},
                "items": [{
                    "content_kind": "chapter_ref",
                    "content_id": None,
                    "title": sub_title or topic_name,
                    "reference": ref,
                    "est_min": read_min_per_sub,
                }],
                "rationale": "Chapter-reference mode — subscribe to unlock LMS content",
                "state": "pending",
                "completed_at": None,
            })
            blocks.append(block)
            remaining -= read_min_per_sub

        if flat and remaining >= practice_min_per_set:
            topic_name, sub, _pw = flat[cursor % len(flat)]
            cursor += 1
            block = _finalize_block({
                "block_id": _new_block_id(),
                "kind": "practice",
                "topic_ref": {"lms_topic_id": None, "name": topic_name},
                "items": [{
                    "content_kind": "mcq_set",
                    "content_id": f"generic:{topic_name}",
                    "topic_id": None,
                    "count": 20,
                    "est_min": practice_min_per_set,
                    "target_accuracy": 70,
                }],
                "rationale": "Generic MCQ practice — open the NEET SS practice bank",
                "state": "pending",
                "completed_at": None,
            })
            blocks.append(block)
            remaining -= practice_min_per_set

        days.append({
            "day": d.isoformat(),
            "phase": "Chapter-mode",
            "time_budget_min": budget,
            "blocks": blocks,
            "checkpoint": {"expected_progress": round((day_offset + 1) / total, 3), "actual": None},
            "_chapter_only": True,
        })
    return days


# ════════════════════════════════════════════════════════════
#  High-level orchestration — single entry point for callers
# ════════════════════════════════════════════════════════════


def build_schedule_from_signal(
    bundle: Dict[str, Any],
    cfg: SchedulerConfig,
    user_signal: Optional[Dict[str, Any]] = None,
    mcq_history: Optional[Dict[str, Any]] = None,
    content_progress: Optional[Dict[str, Any]] = None,
    mock_history: Optional[Dict[str, Any]] = None,
    daily_activity: Optional[Dict[str, Any]] = None,
    fsrs_cards_by_topic: Optional[Dict[str, Dict[str, Any]]] = None,
    due_recall_cards: Optional[List[Dict[str, Any]]] = None,
    user_multipliers: Optional[Dict[str, float]] = None,
    plan_shape: Optional[Dict[str, Any]] = None,
    mastery_vector_override: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    High-level orchestrator planner_v2.py calls when generating a plan.

    Steps:
      1. Build a blended mastery vector from ALL available LMS signals
         (mcq_history + content_progress + mock_history + daily_activity +
         user_signal) plus local FSRS cards. If the caller already has a
         vector (e.g. the route pre-computed one to feed the AI shaper),
         it may pass it via `mastery_vector_override` to skip this step.
      2. Convert the vector into the {topic_id: dict} shape build_schedule
         accepts for its rule-based ranking fallback.
      3. Call build_schedule with the enriched mastery + due cards + the
         optional `plan_shape` blueprint produced by ai/plan_shaper.py.
      4. Return (days, mastery_vector) so the caller can persist both.
    """
    try:
        from app.ai.mastery import build_vector as _build_vector
    except ImportError:  # pragma: no cover
        _build_vector = None

    flat_topics = flatten_topics(bundle) if bundle.get("categories") else []

    if mastery_vector_override is not None:
        mastery_vector = mastery_vector_override
    elif _build_vector is not None:
        mastery_vector = _build_vector(
            lms_signal=user_signal or {},
            mcq_history=mcq_history or {},
            content_progress=content_progress or {},
            mock_history=mock_history or {},
            daily_activity=daily_activity or {},
            fsrs_cards_by_topic=fsrs_cards_by_topic or {},
            bundle_topics=flat_topics,
        )
    else:
        mastery_vector = {}

    # Apply the user's manual focus_topic_ids — boosts gap by 0.2 (capped at 1)
    # so the rotation picks them earlier without nuking the ML signal entirely.
    for tid in cfg.focus_topic_ids or []:
        row = mastery_vector.get(str(tid))
        if row:
            row["gap"] = round(min(1.0, (row.get("gap") or 0) + 0.2), 3)

    # Shape the vector into what build_schedule's mastery dict expects.
    legacy_mastery: Dict[str, Dict[str, Any]] = {}
    total_attempts = 0
    for tid, row in mastery_vector.items():
        drivers = row.get("drivers") or {}
        # The unified mastery model may nest driver groups (mcq/content/mock/...)
        # while the old flat shape put `accuracy` and `attempted` directly on
        # `drivers`. Support both so either caller works.
        mcq_d = drivers.get("mcq") if isinstance(drivers.get("mcq"), dict) else None
        engagement_d = drivers.get("engagement") if isinstance(drivers.get("engagement"), dict) else None
        attempted = int((mcq_d or drivers).get("attempted") or 0)
        total_attempts += attempted
        accuracy_pct = float((mcq_d or drivers).get("accuracy") or 0)
        days_since_last = (engagement_d or drivers).get("last_touched_days_ago")
        if days_since_last is None:
            days_since_last = drivers.get("days_since_last")
        legacy_mastery[str(tid)] = {
            "mastery": float(row.get("mastery") or 0),
            "accuracy": (accuracy_pct / 100.0) if accuracy_pct > 1.0 else accuracy_pct,
            "last_studied_days_ago": days_since_last,
            "gap": float(row.get("gap") or 0),
            "confidence": float(row.get("confidence") or 0),
        }

    days = build_schedule(
        bundle=bundle,
        cfg=cfg,
        mastery=legacy_mastery,
        due_recall_cards=due_recall_cards,
        user_multipliers=user_multipliers,
        plan_shape=plan_shape,
        total_mcq_attempts=total_attempts,
    )
    return days, mastery_vector
