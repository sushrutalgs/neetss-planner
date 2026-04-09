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
    lms_signal: Dict[str, Any],
    fsrs_cards_by_topic: Optional[Dict[str, Dict[str, Any]]] = None,
    bundle_topics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Build a complete `{topic_id: score_topic_result}` mastery vector for
    every topic in the user's bundle. Topics with no LMS data and no
    FSRS card get mastery=0/confidence=0 — the scheduler treats those as
    "not yet started, prioritise for first-pass coverage".

    `lms_signal` is the dict returned by lms_client.get_user_signal().
    `bundle_topics` is the flat list of topic dicts from the syllabus
    bundle (used to fill in zero rows for cold topics).
    """
    fsrs_cards_by_topic = fsrs_cards_by_topic or {}
    by_topic_lms: Dict[str, Dict[str, Any]] = {}
    hint = (lms_signal or {}).get("computed", {}).get("mastery_hint", []) or []
    for row in hint:
        tid = str(row.get("topic_id") or "")
        if tid:
            by_topic_lms[tid] = row

    out: Dict[str, Dict[str, Any]] = {}

    # First pass: every topic that has either LMS history or an FSRS card.
    seen = set()
    for tid, row in by_topic_lms.items():
        out[tid] = score_topic(row, fsrs_cards_by_topic.get(tid))
        out[tid]["topic_id"] = tid
        out[tid]["topic_name"] = row.get("topic_name", "")
        seen.add(tid)
    for tid, card in fsrs_cards_by_topic.items():
        if tid in seen:
            continue
        out[tid] = score_topic(None, card)
        out[tid]["topic_id"] = tid
        out[tid]["topic_name"] = ""
        seen.add(tid)

    # Second pass: zero-fill cold bundle topics so the scheduler has a
    # complete picture (otherwise it would never schedule untouched topics).
    if bundle_topics:
        for t in bundle_topics:
            tid = str(t.get("_id") or t.get("topic_id") or "")
            if not tid or tid in seen:
                continue
            out[tid] = {
                "topic_id": tid,
                "topic_name": t.get("name") or t.get("topic_name") or "",
                "mastery": 0.0,
                "confidence": 0.0,
                "gap": 1.0,
                "drivers": {
                    "lms_mastery": 0.0,
                    "lms_confidence": 0.0,
                    "fsrs_mastery": 0.0,
                    "fsrs_confidence": 0.0,
                    "recency_decay": 0.0,
                    "weight_lms": 1.0,
                    "weight_fsrs": 0.0,
                    "attempted": 0,
                    "accuracy": 0.0,
                    "days_since_last": None,
                    "fsrs_state": None,
                    "fsrs_age_class": "new",
                    "cold": True,
                },
            }
    return out


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
        attempted = int((row.get("drivers") or {}).get("attempted") or 0)
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
