"""
FSRS (Free Spaced Repetition Scheduler) — minimal Python port.

FSRS is the modern successor to SM-2 used by Anki. It models a card with
two state variables — `stability` (days until retention drops to 0.9) and
`difficulty` (intrinsic hardness 1..10) — and computes the next review
interval from a target retention probability.

This module is intentionally framework-free: no DB, no Pydantic. It takes
plain dicts/floats and returns plain dicts/floats. The planner's `models.py`
holds the persistence layer (RecallCard) and calls into here.

We use the FSRS-4.5 default weights and target_retention=0.9, which is the
sweet spot for medical-licensure-style high-stakes content.

Public API:
    init_card(now)                          -> dict
    review_card(card, grade, now)           -> dict   # grade ∈ 0..3 (Again/Hard/Good/Easy)
    days_until_due(card, now)               -> int
    is_due(card, now)                       -> bool

Grade scale (matches the planner UI's 0/3/5 buttons):
    0 -> "Again"   (forgot)         FSRS rating 1
    3 -> "Good"    (recalled)       FSRS rating 3
    5 -> "Easy"    (effortless)     FSRS rating 4
We don't expose Hard (rating 2) — the UI keeps it to 3 buttons.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta
from typing import Dict, Any

# FSRS-4.5 default weights — proven on the open Anki dataset.
W = [
    0.4072, 1.1829, 3.1262, 15.4722, 7.2102, 0.5316, 1.0651, 0.0234,
    1.616, 0.1544, 1.0824, 1.9813, 0.0953, 0.2975, 2.2042, 0.2407, 2.9466,
    0.5034, 0.6567,
]

TARGET_RETENTION = 0.9
DECAY = -0.5  # FSRS forgetting curve exponent
FACTOR = 19.0 / 81.0  # constant from the FSRS retrievability formula

# UI grade -> internal FSRS rating (1=Again, 2=Hard, 3=Good, 4=Easy).
_GRADE_TO_RATING = {0: 1, 1: 1, 2: 2, 3: 3, 4: 3, 5: 4}


def _now() -> datetime:
    return datetime.utcnow()


def _retrievability(stability: float, elapsed_days: float) -> float:
    """Probability the card is still recalled after `elapsed_days`."""
    if stability <= 0:
        return 0.0
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def _next_interval(stability: float) -> int:
    """Days until next review for `target_retention`. Always ≥ 1."""
    if stability <= 0:
        return 1
    interval = (stability / FACTOR) * (TARGET_RETENTION ** (1.0 / DECAY) - 1)
    return max(1, int(round(interval)))


def _init_difficulty(rating: int) -> float:
    d = W[4] - math.exp(W[5] * (rating - 1)) + 1
    return min(10.0, max(1.0, d))


def _init_stability(rating: int) -> float:
    return max(0.1, W[rating - 1])


def _next_difficulty(d: float, rating: int) -> float:
    delta = -W[6] * (rating - 3)
    new_d = d + delta * (10 - d) / 9
    # Mean reversion toward initial difficulty for rating=3 ("Good").
    return min(10.0, max(1.0, W[7] * _init_difficulty(3) + (1 - W[7]) * new_d))


def _next_stability_recall(d: float, s: float, r: float, rating: int) -> float:
    """Stability after a successful review (rating ≥ 2)."""
    hard_penalty = W[15] if rating == 2 else 1.0
    easy_bonus = W[16] if rating == 4 else 1.0
    new_s = s * (
        1
        + math.exp(W[8])
        * (11 - d)
        * (s ** -W[9])
        * (math.exp((1 - r) * W[10]) - 1)
        * hard_penalty
        * easy_bonus
    )
    return max(0.1, new_s)


def _next_stability_forget(d: float, s: float, r: float) -> float:
    """Stability after a lapse (rating = 1)."""
    new_s = (
        W[11]
        * (d ** -W[12])
        * (((s + 1) ** W[13]) - 1)
        * math.exp((1 - r) * W[14])
    )
    return max(0.1, min(s, new_s))


def init_card(now: datetime = None) -> Dict[str, Any]:
    """Create a fresh card. Due immediately so the first study session
    seeds it via review_card()."""
    n = now or _now()
    return {
        "stability": 0.0,
        "difficulty": 0.0,
        "due_at": n.isoformat(),
        "last_reviewed_at": None,
        "reps": 0,
        "lapses": 0,
        "state": "new",  # new | learning | review | relearning
    }


def review_card(
    card: Dict[str, Any],
    grade: int,
    now: datetime = None,
) -> Dict[str, Any]:
    """
    Apply a review with the given UI grade (0/3/5) and return an updated
    card dict. Pure function — caller is responsible for persisting.
    """
    n = now or _now()
    rating = _GRADE_TO_RATING.get(grade, 3)

    if card.get("state") in (None, "new") or card.get("stability", 0) <= 0:
        # First review — bootstrap stability + difficulty from the rating.
        new_d = _init_difficulty(rating)
        new_s = _init_stability(rating)
        next_state = "learning" if rating < 3 else "review"
    else:
        last = card.get("last_reviewed_at")
        if last:
            elapsed = (n - datetime.fromisoformat(last.replace("Z", ""))).total_seconds() / 86400.0
            elapsed = max(0.0, elapsed)
        else:
            elapsed = 0.0
        r = _retrievability(card["stability"], elapsed)
        new_d = _next_difficulty(card["difficulty"], rating)
        if rating == 1:
            new_s = _next_stability_forget(card["difficulty"], card["stability"], r)
            next_state = "relearning"
        else:
            new_s = _next_stability_recall(card["difficulty"], card["stability"], r, rating)
            next_state = "review"

    interval_days = _next_interval(new_s) if rating != 1 else 1
    due_at = n + timedelta(days=interval_days)

    return {
        "stability": round(new_s, 4),
        "difficulty": round(new_d, 4),
        "due_at": due_at.isoformat(),
        "last_reviewed_at": n.isoformat(),
        "reps": int(card.get("reps", 0)) + 1,
        "lapses": int(card.get("lapses", 0)) + (1 if rating == 1 else 0),
        "state": next_state,
        "last_grade": grade,
        "last_interval_days": interval_days,
    }


def days_until_due(card: Dict[str, Any], now: datetime = None) -> int:
    n = now or _now()
    due = card.get("due_at")
    if not due:
        return 0
    delta = (datetime.fromisoformat(due.replace("Z", "")) - n).total_seconds() / 86400.0
    return int(round(delta))


def is_due(card: Dict[str, Any], now: datetime = None) -> bool:
    return days_until_due(card, now) <= 0


def card_age_class(card: Dict[str, Any]) -> str:
    """Anki-style 'young' (<21d stability) vs 'mature' (≥21d). Used by the
    Recall tab stats line."""
    s = card.get("stability", 0)
    if s <= 0:
        return "new"
    return "mature" if s >= 21 else "young"
