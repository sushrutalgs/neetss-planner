"""
Time-to-mastery projection + diminishing returns detection.

Builds on app/ai/bkt.py to answer two questions the dashboard asks:

  1. "How many days until I'm ready?"  → mastery_runway(...)
  2. "Am I still improving on this topic, or stuck?"  → diminishing_returns(...)

The projection uses the user's *measured* daily MCQ throughput (from the
LMS daily-activity feed) instead of an idealised assumption, so the answer
adapts to how much the user actually studies.
"""
from __future__ import annotations
import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.ai.bkt import BKTParams, next_n_to_mastery, p_correct_next


def project_days_to_mastery(
    p_known_by_topic: Dict[str, float],
    daily_mcq_throughput: float,
    target: float = 0.9,
    params_by_topic: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    For each topic, estimate days until p_known crosses `target`. We assume
    practice is evenly distributed across active topics — the planner spreads
    minutes across the user's active focus list, so each topic gets
    `daily_mcq_throughput / n_active_topics` attempts/day.
    """
    out: Dict[str, Dict[str, Any]] = {}
    n_active = max(1, sum(1 for p in p_known_by_topic.values() if p < target))
    per_topic_per_day = max(0.5, daily_mcq_throughput / n_active)
    params_by_topic = params_by_topic or {}

    for tid, p in p_known_by_topic.items():
        params = BKTParams.from_dict(params_by_topic.get(tid))
        attempts_needed = next_n_to_mastery(p, params, target=target)
        days = math.ceil(attempts_needed / per_topic_per_day) if per_topic_per_day > 0 else None
        out[tid] = {
            "p_known": round(p, 4),
            "attempts_needed": attempts_needed,
            "days_to_target": days,
            "p_correct_next": round(p_correct_next(p, params), 4),
            "target": target,
        }
    return out


def mastery_runway(
    p_known_by_topic: Dict[str, float],
    end_date: date,
    daily_mcq_throughput: float,
    target: float = 0.9,
    params_by_topic: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """
    Aggregate runway: returns days_remaining vs days_required, and a
    `feasible` flag the dashboard uses to colour the readiness ring.
    """
    today = date.today()
    days_remaining = max(0, (end_date - today).days)
    by_topic = project_days_to_mastery(
        p_known_by_topic, daily_mcq_throughput, target, params_by_topic
    )
    # Bottleneck = topic that needs the most days under current throughput.
    bottleneck_days = 0
    bottleneck_topic = None
    sum_attempts = 0
    for tid, row in by_topic.items():
        sum_attempts += row.get("attempts_needed") or 0
        d = row.get("days_to_target") or 0
        if d > bottleneck_days:
            bottleneck_days = d
            bottleneck_topic = tid

    return {
        "days_remaining": days_remaining,
        "days_required_bottleneck": bottleneck_days,
        "bottleneck_topic_id": bottleneck_topic,
        "feasible": bottleneck_days <= days_remaining,
        "total_attempts_needed": sum_attempts,
        "average_p_known": round(
            sum(p_known_by_topic.values()) / max(1, len(p_known_by_topic)), 4
        ),
        "by_topic": by_topic,
    }


def diminishing_returns(
    history_buckets: List[Dict[str, Any]],
    window_size: int = 50,
    delta_threshold: float = 0.02,
) -> Dict[str, Any]:
    """
    Walks the last 3 windows of `window_size` attempts and computes the
    p_known delta across windows. If the delta has flattened below
    `delta_threshold` for the last 2 windows, we flag diminishing returns
    so the planner can rotate the user to a different topic.

    `history_buckets` shape (one row per attempt, time-ordered):
        [{"p_known_after": float, "ts": datetime}, ...]
    """
    if len(history_buckets) < window_size * 2:
        return {"flag": False, "reason": "insufficient_history", "deltas": []}

    windows: List[float] = []
    for i in range(len(history_buckets), 0, -window_size):
        chunk = history_buckets[max(0, i - window_size):i]
        if not chunk:
            continue
        windows.append(chunk[-1].get("p_known_after", 0.0))
        if len(windows) >= 4:
            break

    if len(windows) < 3:
        return {"flag": False, "reason": "insufficient_windows", "deltas": []}

    deltas = [windows[i] - windows[i + 1] for i in range(len(windows) - 1)]
    last_two_flat = all(d < delta_threshold for d in deltas[:2])
    return {
        "flag": last_two_flat,
        "reason": "plateau" if last_two_flat else "still_improving",
        "deltas": [round(d, 4) for d in deltas],
        "current_p_known": round(windows[0], 4),
        "recommend_rotate": last_two_flat,
    }


def slope_estimate(daily_mastery_series: List[Tuple[date, float]]) -> float:
    """
    Linear-regression slope of mastery over the last N days. Positive =
    improving, near-zero = plateau, negative = forgetting.
    """
    if len(daily_mastery_series) < 3:
        return 0.0
    xs = list(range(len(daily_mastery_series)))
    ys = [m for _, m in daily_mastery_series]
    n = len(xs)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    return num / den if den > 1e-9 else 0.0
