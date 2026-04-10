"""
Mastery model — reconciles the LMS-side prior with planner-local FSRS state.

The LMS exposes per-topic accuracy stats via /api/planner/user-mcq-history
and /api/planner/user-signal. That gives us a strong starting prior:

    mastery_lms ∈ [0,1]   confidence_lms ∈ [0,1]

But MCQ accuracy alone is a leaky signal — students forget, students get
lucky, and there's no model of "have you actually re-seen this recently?"
That's what spaced repetition adds. We blend the LMS prior with the local
FSRS recall stability:

    mastery_blended = w_lms * mastery_lms + w_fsrs * mastery_fsrs

where mastery_fsrs is computed from the FSRS card stability (a card with
30+ days of stability and recent successful recall = high mastery, a card
with frequent lapses = low mastery, regardless of MCQ accuracy).

The blend weight tilts toward FSRS as the card matures and toward the LMS
prior when there's no FSRS history yet. This handles three cases cleanly:

  1. New user, never used the planner — pure LMS prior (FSRS empty)
  2. Returning user, mature cards — heavily FSRS-weighted
  3. Mid-game, mixed — smooth blend

The output is consumed by:
  - content_scheduler.py to prioritise topics (low mastery + high weight first)
  - app/ai/recommender.py as the input to "what to improve" prompts
  - routers/planner_v2.py via /api/ml/mastery for the Progress radar

Public API:
    score_topic(lms_row, fsrs_card) -> { mastery, confidence, drivers }
    rank_weakness(mastery_vector, weights) -> [topic_id, ...]
    coverage_pct(mastery_vector) -> float (0..1)
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional

from app.ai.spaced_repetition import card_age_class

# Tunables — chosen by gut from a small offline sweep, not learned. Easy
# to swap for a real fitted model later without changing the call sites.
W_LMS_BASE = 0.6        # baseline weight on the LMS prior
W_FSRS_RAMP = 0.04      # how fast FSRS overrides as stability grows (per day)
RECENCY_HALFLIFE_DAYS = 30  # mastery decays without interaction
MIN_CONFIDENCE = 0.05


def _fsrs_mastery(card: Optional[Dict[str, Any]]) -> tuple[float, float]:
    """
    Convert an FSRS card into a (mastery, confidence) pair.

    Mastery scales with stability (logistic from 0 → 1 around 21d, the
    Anki "mature" threshold). Confidence scales with reps and shrinks
    with lapse ratio.
    """
    if not card or card.get("stability", 0) <= 0:
        return 0.0, 0.0
    s = float(card.get("stability", 0))
    reps = int(card.get("reps", 0))
    lapses = int(card.get("lapses", 0))
    # Logistic on stability: 0.5 at 21d (mature threshold), saturating
    # toward 1 by ~90d.
    mastery = 1.0 / (1.0 + math.exp(-(s - 21.0) / 10.0))
    # Lapse ratio penalty: 3 lapses out of 10 reps drops mastery ~25%.
    if reps > 0:
        mastery *= max(0.4, 1.0 - 0.6 * (lapses / reps))
    # Confidence rises with reps but is capped.
    confidence = min(1.0, reps / 8.0)
    return round(mastery, 3), round(confidence, 3)


def _recency_decay(days_ago: Optional[int]) -> float:
    """Multiplier 0..1 — full strength if seen today, halved at 30d, ~0 at 120d."""
    if days_ago is None:
        return 0.6  # never seen → assume mid-decay
    if days_ago <= 0:
        return 1.0
    return max(0.05, math.pow(0.5, days_ago / RECENCY_HALFLIFE_DAYS))


def score_topic(
    lms_row: Optional[Dict[str, Any]],
    fsrs_card: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute the blended mastery score for one topic.

    `lms_row` is one entry from /api/planner/user-mcq-history `by_topic` or
    /api/planner/user-signal `computed.mastery_hint`. Either of the two
    inputs may be None.
    """
    # Pull LMS prior.
    if lms_row:
        lms_m = float(lms_row.get("mastery") or 0)
        lms_c = float(lms_row.get("confidence") or 0)
        attempted = int(lms_row.get("attempted") or 0)
        days_ago = lms_row.get("days_since_last") or lms_row.get("last_seen_days_ago")
        days_ago = int(days_ago) if days_ago is not None else None
        accuracy = float(lms_row.get("accuracy") or 0)
    else:
        lms_m = 0.0
        lms_c = 0.0
        attempted = 0
        days_ago = None
        accuracy = 0.0

    # Pull FSRS contribution.
    fsrs_m, fsrs_c = _fsrs_mastery(fsrs_card)

    # Recency decay applied to the LMS prior — accuracy stale by 60+ days
    # is much weaker evidence than fresh accuracy.
    decay = _recency_decay(days_ago)
    lms_m_decayed = lms_m * decay
    lms_c_decayed = lms_c * decay

    # Blend weights — pure LMS when no FSRS, ramp toward FSRS as card matures.
    if fsrs_card and fsrs_card.get("stability", 0) > 0:
        s = float(fsrs_card["stability"])
        w_fsrs = min(0.85, W_LMS_BASE * 0 + s * W_FSRS_RAMP)
        w_lms = 1.0 - w_fsrs
    else:
        w_fsrs = 0.0
        w_lms = 1.0

    blended_m = round(w_lms * lms_m_decayed + w_fsrs * fsrs_m, 3)
    blended_c = round(max(MIN_CONFIDENCE, w_lms * lms_c_decayed + w_fsrs * fsrs_c), 3)

    return {
        "mastery": blended_m,
        "confidence": blended_c,
        "gap": round(max(0.0, 1.0 - blended_m), 3),
        "drivers": {
            "lms_mastery": round(lms_m, 3),
            "lms_confidence": round(lms_c, 3),
            "fsrs_mastery": fsrs_m,
            "fsrs_confidence": fsrs_c,
            "recency_decay": round(decay, 3),
            "weight_lms": round(w_lms, 3),
            "weight_fsrs": round(w_fsrs, 3),
            "attempted": attempted,
            "accuracy": accuracy,
            "days_since_last": days_ago,
            "fsrs_state": (fsrs_card or {}).get("state"),
            "fsrs_age_class": card_age_class(fsrs_card or {}),
        },
    }


def build_vector(
    lms_signal: Optional[Dict[str, Any]] = None,
    fsrs_cards_by_topic: Optional[Dict[str, Dict[str, Any]]] = None,
    bundle_topics: Optional[List[Dict[str, Any]]] = None,
    *,
    mcq_history: Optional[Dict[str, Any]] = None,
    content_progress: Optional[Dict[str, Any]] = None,
    mock_history: Optional[Dict[str, Any]] = None,
    daily_activity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Unified mastery vector. Builds a complete `{topic_id: row}` map where
    each row blends every LMS signal the planner has access to:

      - `lms_signal.computed.mastery_hint` — server-side composite prior
      - `mcq_history.by_topic`             — recency-weighted accuracy
      - `content_progress.by_topic`        — video/notes coverage
      - `mock_history`                     — aggregate mock weight (flat)
      - `daily_activity`                   — engagement score
      - `fsrs_cards_by_topic`              — spaced-repetition stability

    Row shape:

        {
          "topic_id":   "...",
          "topic_name": "...",
          "mastery":    0.0..1.0,
          "confidence": 0.0..1.0,
          "gap":        0.0..1.0,   # boosted when attempted < 5
          "drivers": {
            "mcq":        { attempted, correct, accuracy, recency_weighted_acc, time_per_q },
            "content":    { videos_watched, videos_total, notes_opened, notes_total, coverage_pct, time_spent_min },
            "mock":       { attempts, best_percentile, topic_slice_accuracy },
            "engagement": { last_touched_days_ago, activity_score_14d },
            "fsrs":       { stability, reps, lapses, state, age_class },
          }
        }

    Topics with no signal at all get a zero-mastery row marked `cold=True`
    so the scheduler knows to prioritise them for Foundation coverage.
    """
    fsrs_cards_by_topic = fsrs_cards_by_topic or {}
    bundle_topics = bundle_topics or []

    # ── 1. Index all signals by topic_id ─────────────────────────────────
    hint_by_topic: Dict[str, Dict[str, Any]] = {}
    hint = (lms_signal or {}).get("computed", {}).get("mastery_hint", []) or []
    for row in hint:
        tid = str(row.get("topic_id") or "")
        if tid:
            hint_by_topic[tid] = row

    mcq_by_topic: Dict[str, Dict[str, Any]] = {}
    for row in (mcq_history or {}).get("by_topic", []) or []:
        tid = str(row.get("topic_id") or "")
        if tid:
            mcq_by_topic[tid] = row

    content_by_topic: Dict[str, Dict[str, Any]] = {}
    for row in (content_progress or {}).get("by_topic", []) or []:
        tid = str(row.get("topic_id") or "")
        if tid:
            content_by_topic[tid] = row

    # Mock history is flat — we aggregate to a single "mock weight" used as
    # a global nudge. A per-topic slice would need a richer LMS payload.
    mock_totals = (mock_history or {}).get("totals") or {}
    global_mock_attempts = int(mock_totals.get("attempts") or len((mock_history or {}).get("mocks") or []))
    latest_predicted_rank = (mock_history or {}).get("latest_predicted_rank")

    activity_14d = int((daily_activity or {}).get("avg_minutes_last_14d") or 0)
    streak_current = int(((daily_activity or {}).get("streak") or {}).get("current") or 0)

    # Bundle topic meta for zero-filling and name lookups.
    meta_by_topic: Dict[str, Dict[str, Any]] = {}
    for t in bundle_topics:
        tid = str(t.get("_id") or t.get("topic_id") or "")
        if tid:
            meta_by_topic[tid] = t

    all_tids: set[str] = set()
    all_tids.update(hint_by_topic.keys())
    all_tids.update(mcq_by_topic.keys())
    all_tids.update(content_by_topic.keys())
    all_tids.update(fsrs_cards_by_topic.keys())
    all_tids.update(meta_by_topic.keys())

    out: Dict[str, Dict[str, Any]] = {}

    for tid in all_tids:
        meta = meta_by_topic.get(tid) or {}
        topic_name = (
            (hint_by_topic.get(tid) or {}).get("topic_name")
            or (mcq_by_topic.get(tid) or {}).get("topic_name")
            or meta.get("name")
            or meta.get("topic_name")
            or ""
        )

        # ── MCQ slice ──
        mcq_row = mcq_by_topic.get(tid) or {}
        mcq_attempted = int(mcq_row.get("attempted") or 0)
        mcq_correct = int(mcq_row.get("correct") or 0)
        mcq_accuracy = float(mcq_row.get("accuracy") or 0)  # 0..100 (LMS convention)
        if mcq_accuracy <= 1.0 and mcq_accuracy > 0:
            mcq_accuracy *= 100.0
        recency_weighted_acc = float(mcq_row.get("recency_weighted_accuracy") or mcq_row.get("mastery") or 0)
        if recency_weighted_acc <= 1.0:
            recency_weighted_acc *= 100.0
        time_per_q = float(mcq_row.get("avg_time_per_question") or 0)
        mcq_days_ago = mcq_row.get("days_since_last") or mcq_row.get("last_seen_days_ago")

        # ── Content slice ──
        content_row = content_by_topic.get(tid) or {}
        videos_watched = int(content_row.get("videos_watched") or 0)
        videos_total = int(content_row.get("videos_total") or (meta.get("content_counts", {}) or {}).get("videos") or 0)
        notes_opened = int(content_row.get("notes_opened") or 0)
        notes_total = int(content_row.get("notes_total") or (meta.get("content_counts", {}) or {}).get("notes") or 0)
        content_time_min = float(content_row.get("time_spent_minutes") or content_row.get("time_spent_min") or 0)
        content_coverage = 0.0
        if videos_total + notes_total > 0:
            content_coverage = (videos_watched + notes_opened) / (videos_total + notes_total)

        # ── FSRS slice ──
        fsrs_card = fsrs_cards_by_topic.get(tid)
        fsrs_m, fsrs_c = _fsrs_mastery(fsrs_card)

        # ── Hint slice (LMS-side prior from user-signal) ──
        hint_row = hint_by_topic.get(tid) or {}
        hint_mastery = float(hint_row.get("mastery") or 0)
        hint_confidence = float(hint_row.get("confidence") or 0)
        hint_days_ago = hint_row.get("last_seen_days_ago") or hint_row.get("days_since_last")

        days_since_last = None
        for candidate in (mcq_days_ago, hint_days_ago):
            if candidate is not None:
                days_since_last = int(candidate)
                break

        # ── Composite mastery ──
        # Weights auto-scale based on available evidence. Missing slices get
        # their weight redistributed so the score doesn't collapse to zero.
        w_mcq = 0.45 if mcq_attempted >= 5 else (0.20 if mcq_attempted > 0 else 0.0)
        w_content = 0.20 if (videos_total + notes_total) > 0 else 0.0
        w_mock = 0.10 if global_mock_attempts > 0 else 0.0
        w_fsrs = 0.25 if (fsrs_card and float(fsrs_card.get("stability", 0)) > 0) else 0.0
        w_hint = max(0.0, 1.0 - (w_mcq + w_content + w_mock + w_fsrs))

        decay = _recency_decay(days_since_last)

        # Each sub-score is in [0, 1].
        s_mcq = (recency_weighted_acc / 100.0) * decay
        s_content = min(1.0, content_coverage)
        # Mock slice — we don't have per-topic mock accuracy, so this is a
        # tiny global nudge (0.5 as a neutral baseline if the user has taken
        # any mock, 0 otherwise). Intentionally mild.
        s_mock = 0.5 if global_mock_attempts > 0 else 0.0
        s_fsrs = fsrs_m
        s_hint = hint_mastery * decay

        mastery_raw = (
            w_mcq * s_mcq
            + w_content * s_content
            + w_mock * s_mock
            + w_fsrs * s_fsrs
            + w_hint * s_hint
        )
        mastery = round(max(0.0, min(1.0, mastery_raw)), 3)

        # Confidence rises with evidence density.
        evidence_bits = (
            min(1.0, mcq_attempted / 20.0)
            + min(1.0, (videos_watched + notes_opened) / 8.0)
            + min(1.0, global_mock_attempts / 4.0)
            + (fsrs_c or 0)
            + (hint_confidence or 0) * decay
        )
        confidence = round(max(MIN_CONFIDENCE, min(1.0, evidence_bits / 5.0)), 3)

        # Gap — boosted when cold so Foundation phase picks it.
        gap = round(max(0.0, 1.0 - mastery), 3)
        if mcq_attempted < 5 and (videos_watched + notes_opened) < 2:
            gap = round(min(1.0, gap + 0.15), 3)

        out[tid] = {
            "topic_id": tid,
            "topic_name": topic_name,
            "mastery": mastery,
            "confidence": confidence,
            "gap": gap,
            "drivers": {
                "mcq": {
                    "attempted": mcq_attempted,
                    "correct": mcq_correct,
                    "accuracy": round(mcq_accuracy, 2),
                    "recency_weighted_acc": round(recency_weighted_acc, 2),
                    "time_per_q": round(time_per_q, 2),
                },
                "content": {
                    "videos_watched": videos_watched,
                    "videos_total": videos_total,
                    "notes_opened": notes_opened,
                    "notes_total": notes_total,
                    "coverage_pct": round(content_coverage, 3),
                    "time_spent_min": round(content_time_min, 1),
                },
                "mock": {
                    "attempts": global_mock_attempts,
                    "latest_predicted_rank": latest_predicted_rank,
                },
                "engagement": {
                    "last_touched_days_ago": days_since_last,
                    "activity_score_14d": activity_14d,
                    "streak_current": streak_current,
                },
                "fsrs": {
                    "stability": float((fsrs_card or {}).get("stability") or 0),
                    "reps": int((fsrs_card or {}).get("reps") or 0),
                    "lapses": int((fsrs_card or {}).get("lapses") or 0),
                    "state": (fsrs_card or {}).get("state"),
                    "age_class": card_age_class(fsrs_card or {}),
                },
                # Back-compat flat keys — the content_scheduler adapter reads
                # these for the legacy mastery dict shape.
                "attempted": mcq_attempted,
                "accuracy": round(mcq_accuracy, 2),
                "days_since_last": days_since_last,
                "weights": {
                    "mcq": round(w_mcq, 2),
                    "content": round(w_content, 2),
                    "mock": round(w_mock, 2),
                    "fsrs": round(w_fsrs, 2),
                    "hint": round(w_hint, 2),
                },
                "recency_decay": round(decay, 3),
                "cold": (mcq_attempted < 5 and (videos_watched + notes_opened) < 2),
            },
        }
    return out


# ────────────── helper rankers used by recommender + shaper ──────────────


def rank_cold_topics(
    vector: Dict[str, Dict[str, Any]],
    bundle_topics: Optional[List[Dict[str, Any]]] = None,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Zero-evidence topics ordered by priority_label then bundle order."""
    bundle_topics = bundle_topics or []
    priority_by_id: Dict[str, float] = {}
    for t in bundle_topics:
        tid = str(t.get("_id") or t.get("topic_id") or "")
        if tid:
            pl = t.get("priority_label")
            if not pl:
                priority_by_id[tid] = 1.0
            elif "P1" in str(pl).upper() or "HIGH" in str(pl).upper():
                priority_by_id[tid] = 3.0
            elif "P2" in str(pl).upper() or "MED" in str(pl).upper():
                priority_by_id[tid] = 2.0
            else:
                priority_by_id[tid] = 1.0
    cold = [
        r for r in vector.values()
        if (r.get("drivers") or {}).get("cold")
    ]
    cold.sort(key=lambda r: -priority_by_id.get(r.get("topic_id", ""), 1.0))
    return cold[:top_n]


def rank_refresh(
    vector: Dict[str, Dict[str, Any]],
    stale_days: int = 21,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Topics with high mastery but no recent interaction — refresh candidates."""
    out = []
    for r in vector.values():
        if (r.get("mastery") or 0) < 0.6:
            continue
        drivers = r.get("drivers") or {}
        eng = drivers.get("engagement") if isinstance(drivers.get("engagement"), dict) else {}
        days = eng.get("last_touched_days_ago") if eng else drivers.get("days_since_last")
        if days is None or days < stale_days:
            continue
        out.append(r)
    out.sort(key=lambda r: -((r.get("drivers") or {}).get("engagement", {}).get("last_touched_days_ago") or 0))
    return out[:top_n]


def rank_weakness(
    vector: Dict[str, Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
    min_attempts: int = 5,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """
    Top-N topics most worth working on right now.

    Score = gap × topic_weight × (1 − exp(−attempted/8))

    The exp factor down-weights "I got 1/2 wrong on this topic" so the
    list isn't dominated by noise. Topics with attempted < min_attempts
    are excluded — for those we want coverage-based prioritisation, not
    weakness-based.
    """
    weights = weights or {}
    scored = []
    for tid, row in vector.items():
        drivers = row.get("drivers") or {}
        mcq_d = drivers.get("mcq") if isinstance(drivers.get("mcq"), dict) else None
        attempted = int((mcq_d or drivers).get("attempted") or 0)
        if attempted < min_attempts:
            continue
        gap = float(row.get("gap") or 0)
        if gap <= 0:
            continue
        w = float(weights.get(tid, 1.0))
        confidence_floor = max(0.2, float(row.get("confidence") or 0))
        signal = 1.0 - math.exp(-attempted / 8.0)
        score = gap * w * confidence_floor * signal
        scored.append({**row, "weakness_score": round(score, 4)})
    scored.sort(key=lambda r: r["weakness_score"], reverse=True)
    return scored[:top_n]


def coverage_pct(vector: Dict[str, Dict[str, Any]]) -> float:
    """Fraction of topics with mastery ≥ 0.5 (the 'started seriously' line)."""
    if not vector:
        return 0.0
    started = sum(1 for r in vector.values() if (r.get("mastery") or 0) >= 0.5)
    return round(started / len(vector), 3)


def avg_mastery(vector: Dict[str, Dict[str, Any]]) -> float:
    if not vector:
        return 0.0
    total = sum(float(r.get("mastery") or 0) for r in vector.values())
    return round(total / len(vector), 3)
