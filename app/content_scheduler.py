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
MAX_VIDEO_MIN_PER_DAY = 45

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
    avg_per_note = max(8, int((est.get("read") or 22) / max(1, len(notes))))
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
    return _finalize_block({
        "block_id": _new_block_id(),
        "kind": "read",
        "topic_ref": {"lms_topic_id": str(topic.get("_id")), "name": topic.get("name", "")},
        "items": items,
        "rationale": "Foundation reading",
        "state": "pending",
        "completed_at": None,
    })


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
    avg_per_video = max(6, int((est.get("watch") or 20) / max(1, len(videos))))
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
    Return a list of day dicts from cfg.start_date through cfg.end_date.

    `plan_shape` is the optional AI-shaped plan blueprint from ai/plan_shaper.py:
        {
          "phase_windows": [{"phase": "...", "days": int}, ...],
          "ordered_topic_ids": ["...", ...],
          "weak_blitzes":     [{"topic_id": "...", "days": [int, ...]}],
          "diagnostic_week":  [{"day_offset": int, "topic_ids": [...], "n": int}]
        }
    When absent, the scheduler falls back to its own ranking.

    `total_mcq_attempts` is the student's lifetime MCQ attempt count across
    the LMS. When below COLD_START_MCQ_THRESHOLD, the first few days inject
    diagnostic mini-mocks on high-priority topics so mastery can bootstrap.
    """
    mastery = mastery or {}
    due_recall_cards = due_recall_cards or []
    user_multipliers = user_multipliers or {"read": 1.0, "watch": 1.0, "mcq": 1.0}

    if not cfg.use_lms_content or not bundle.get("categories"):
        return _build_chapter_only_schedule(cfg, user_multipliers)

    topics = flatten_topics(bundle)
    topics_by_id = {str(t.get("_id") or ""): t for t in topics}
    mocks = collect_mocks(bundle)

    # Resolve an ordered topic queue. If the AI shaper has given us one, use it
    # (with shape-unknown topics appended at the end in rule-based order so we
    # never silently drop content). Otherwise use the internal _topic_score.
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
        # Append anything the shaper didn't mention, ranked by rule.
        tail = sorted(
            (t for t in topics if str(t.get("_id") or "") not in seen),
            key=lambda t: -_topic_score(t, mastery, cfg.start_date),
        )
        ordered_topics.extend(tail)
    else:
        ordered_topics = sorted(topics, key=lambda t: -_topic_score(t, mastery, cfg.start_date))

    # Phase window override from the shaper (days-per-phase). We convert it
    # into a [(phase, end_day_offset), ...] cut list the per-day loop reads.
    shape_windows = shape.get("phase_windows") or []
    phase_cuts: List[tuple[str, int]] = []
    if shape_windows:
        acc = 0
        for w in shape_windows:
            acc += int(w.get("days") or 0)
            phase_cuts.append((str(w.get("phase") or "Foundation"), acc))

    def _phase_for_day(day_offset: int, days_left: int, used: set[str]) -> str:
        if phase_cuts:
            for name, end in phase_cuts:
                if day_offset < end:
                    return name
            return phase_cuts[-1][0]
        return _phase_for_coverage(_compute_p1_coverage(topics, used), days_left)

    # Build a quick-lookup of which offsets are blitz days for which topics.
    blitz_by_offset: Dict[int, List[str]] = {}
    for b in shape.get("weak_blitzes") or []:
        tid = str(b.get("topic_id") or "")
        for off in b.get("days") or []:
            blitz_by_offset.setdefault(int(off), []).append(tid)

    # Diagnostic week: either shaper-supplied or auto-injected for cold users.
    diag_by_offset: Dict[int, List[str]] = {}
    shape_diag = shape.get("diagnostic_week") or []
    if shape_diag:
        for e in shape_diag:
            diag_by_offset[int(e.get("day_offset") or 0)] = [str(t) for t in (e.get("topic_ids") or [])]
    elif total_mcq_attempts < COLD_START_MCQ_THRESHOLD:
        # Pick top-5 P1 topics (ignoring focus list; shaper handles nuance) for
        # the first DIAGNOSTIC_DAYS study days.
        p1_topics = [t for t in ordered_topics if _priority_weight(t.get("priority_label")) >= 3.0]
        for i in range(min(DIAGNOSTIC_DAYS, len(p1_topics))):
            diag_by_offset[i] = [str(p1_topics[i].get("_id") or "")]

    days_total = (cfg.end_date - cfg.start_date).days + 1
    days: List[Dict[str, Any]] = []
    used_content: set[str] = set()
    recall_pool = list(due_recall_cards)

    rotation_idx = 0
    study_day_counter = 0  # counts only non-rest days (for cold-start diag)

    for day_offset in range(days_total):
        d = cfg.start_date + timedelta(days=day_offset)
        days_left = (cfg.end_date - d).days

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

        phase = _phase_for_day(day_offset, days_left, used_content)
        budget = cfg.effective_daily_minutes
        remaining = budget
        day_video_used = 0
        blocks: List[Dict[str, Any]] = []

        # 1) Recall first — always pin a small recall block at the top
        chunk = recall_pool[:8]
        recall_pool = recall_pool[8:]
        recall_block = _build_recall_block(chunk)
        if recall_block:
            blocks.append(recall_block)
            remaining -= int(recall_block.get("minutes") or 0)

        # 2a) Diagnostic mini-mock (cold-start week-1). One per study day.
        diag_tids = diag_by_offset.get(study_day_counter, [])
        for diag_tid in diag_tids[:1]:
            diag_topic = topics_by_id.get(diag_tid)
            if diag_topic:
                mm = _build_mini_mock_block(diag_topic, DIAGNOSTIC_MINI_MOCK_Q, user_multipliers["mcq"])
                blocks.append(mm)
                remaining -= int(mm.get("minutes") or 0)

        # 2b) Full mock days — Final phase gets one every 3 days (if available)
        if phase == "Final" and mocks and day_offset % 3 == 0:
            mock = mocks[(day_offset // 3) % len(mocks)]
            mock_block = _build_mock_block(mock, user_multipliers["mcq"])
            blocks.append(mock_block)
            remaining -= int(mock_block.get("minutes") or 0)

        # 3) Topic picks for the day.
        rotation_count = PHASE_TOPIC_ROTATION_RATE[phase]
        picked: List[Dict[str, Any]] = []

        # Blitz topics always land first on their scheduled offset.
        blitz_tids = blitz_by_offset.get(day_offset, [])
        for tid in blitz_tids:
            t = topics_by_id.get(str(tid))
            if t is not None and t not in picked:
                picked.append(t)

        # Round-robin from the ordered queue for the remaining slots.
        remaining_slots = max(0, rotation_count - len(picked))
        if ordered_topics:
            for i in range(remaining_slots):
                picked.append(ordered_topics[(rotation_idx + i) % len(ordered_topics)])
            rotation_idx = (rotation_idx + remaining_slots) % max(1, len(ordered_topics))

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
                spent = int(builder.get("minutes") or 0)
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
