"""
Peer benchmarking — cohort percentile + relative-strength signals.

The LMS exposes a /api/planner/cohort-stats endpoint that returns aggregate
distributions for the user's exam_type cohort (NEET SS or INI SS-ET) gated
on `leaderboard_opt_in`. This module turns that into:

  • percentile rank for the user across mastery, mock score, daily minutes
  • topic-level relative strength (am I above or below the cohort?)
  • trending weak topics (cohort-wide accuracy dropping fast)

We do not collect or send any PII. Cohort stats are pre-aggregated by the
LMS — the planner only ever sees distributions, never user identities.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional


logger = logging.getLogger("planner.ai.peer")


def percentile_in_distribution(value: float, distribution: List[float]) -> float:
    """Returns the percentile (0-100) of `value` inside the distribution."""
    if not distribution:
        return 50.0
    sorted_vals = sorted(distribution)
    n = len(sorted_vals)
    below = sum(1 for v in sorted_vals if v < value)
    return round(100.0 * below / n, 1)


def benchmark_user(
    user_metrics: Dict[str, float],
    cohort_distributions: Dict[str, List[float]],
) -> Dict[str, Any]:
    """
    `user_metrics`: { mastery_avg, coverage_pct, mock_accuracy, avg_min_14d }
    `cohort_distributions`: same keys, list of values from the cohort.
    """
    out: Dict[str, Any] = {}
    for key, value in user_metrics.items():
        dist = cohort_distributions.get(key, [])
        pct = percentile_in_distribution(float(value or 0), dist)
        out[key] = {
            "value": value,
            "percentile": pct,
            "cohort_p50": _percentile_value(dist, 50),
            "cohort_p90": _percentile_value(dist, 90),
            "verdict": _verdict(pct),
        }
    out["overall_percentile"] = round(
        sum(v["percentile"] for v in out.values()) / max(1, len(out)), 1
    )
    return out


def _percentile_value(dist: List[float], pct: float) -> Optional[float]:
    if not dist:
        return None
    s = sorted(dist)
    idx = int(len(s) * (pct / 100.0))
    idx = max(0, min(len(s) - 1, idx))
    return round(s[idx], 2)


def _verdict(pct: float) -> str:
    if pct >= 80:
        return "top_tier"
    if pct >= 60:
        return "above_avg"
    if pct >= 40:
        return "median"
    if pct >= 20:
        return "below_avg"
    return "bottom_quartile"


def topic_relative_strength(
    user_mastery_vector: Dict[str, Dict[str, Any]],
    cohort_topic_means: Dict[str, float],
) -> List[Dict[str, Any]]:
    """
    For each topic, compare the user's mastery against the cohort mean.
    Returns a sorted list (most differential first) so the UI can show
    "where you're ahead" and "where you're behind".
    """
    rows = []
    for tid, urow in (user_mastery_vector or {}).items():
        cohort_mean = cohort_topic_means.get(tid)
        if cohort_mean is None:
            continue
        user_m = (urow or {}).get("mastery", 0.0) or 0.0
        delta = user_m - cohort_mean
        rows.append({
            "topic_id": tid,
            "topic_name": (urow or {}).get("topic_name") or tid,
            "user_mastery": round(user_m, 3),
            "cohort_mean": round(cohort_mean, 3),
            "delta": round(delta, 3),
            "verdict": "ahead" if delta > 0.05 else ("behind" if delta < -0.05 else "even"),
        })
    rows.sort(key=lambda r: -abs(r["delta"]))
    return rows


def trending_weak_topics(cohort_topic_trends: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Cohort-wide topics whose accuracy is dropping fastest week-over-week.
    These are the topics worth bumping up your own priority on.
    """
    out = sorted(
        cohort_topic_trends or [],
        key=lambda r: (r.get("delta_7d") or 0),
    )
    return out[:8]
