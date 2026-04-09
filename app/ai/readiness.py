"""
Readiness score — the single number that drives the Dashboard hero ring.

Multi-factor weighted composite, normalised to [0, 100]:

  factor                         weight   source
  ──────                         ──────   ──────
  average mastery                 0.35    mastery_vector
  coverage % of syllabus          0.20    mastery_vector
  recent mock accuracy (rolling)  0.20    user-mock-history
  consistency (avg min/day 14d)   0.10    user-daily-activity
  recall health (FSRS due rate)   0.10    RecallCard rows
  days_remaining buffer           0.05    plan.end_date - today

Output:
  {
    "score": 73,
    "band":  "yellow",   # green ≥80, yellow 60-79, red <60
    "factors": [...],
    "recommendation": "Focus on coverage — you've only touched 41% of the syllabus."
  }
"""
from __future__ import annotations
import math
from datetime import date
from typing import Any, Dict, List, Optional


def _band(score: float) -> str:
    if score >= 80:
        return "green"
    if score >= 60:
        return "yellow"
    return "red"


def _consistency_score(avg_minutes_14d: float, target_minutes: float = 240) -> float:
    """0..1 — sigmoid that hits 0.9 at 4h/day, 0.5 at 2h/day."""
    if avg_minutes_14d <= 0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-(avg_minutes_14d - target_minutes / 2) / (target_minutes / 6)))


def _runway_score(days_remaining: int, days_required: int) -> float:
    """1.0 if you have ≥1.5x buffer, 0.0 if you're behind, linear in between."""
    if days_required <= 0:
        return 1.0
    ratio = days_remaining / days_required
    if ratio >= 1.5:
        return 1.0
    if ratio <= 0.7:
        return 0.0
    return (ratio - 0.7) / 0.8


def compute(
    avg_mastery: float,
    coverage_pct: float,
    recent_mock_accuracy_pct: float,
    avg_minutes_14d: float,
    recall_health_pct: float,
    days_remaining: int,
    days_required_bottleneck: int,
) -> Dict[str, Any]:
    """
    All inputs in their natural units. Returns the composite readiness score
    plus the per-factor breakdown so the UI can show the contribution bars.
    """
    f_mastery = max(0.0, min(1.0, avg_mastery))
    f_coverage = max(0.0, min(1.0, coverage_pct))
    f_mock = max(0.0, min(1.0, recent_mock_accuracy_pct / 100.0))
    f_consistency = _consistency_score(avg_minutes_14d)
    f_recall = max(0.0, min(1.0, recall_health_pct))
    f_runway = _runway_score(days_remaining, days_required_bottleneck)

    weights = {
        "mastery": 0.35,
        "coverage": 0.20,
        "mock": 0.20,
        "consistency": 0.10,
        "recall": 0.10,
        "runway": 0.05,
    }
    contributions = {
        "mastery": f_mastery * weights["mastery"],
        "coverage": f_coverage * weights["coverage"],
        "mock": f_mock * weights["mock"],
        "consistency": f_consistency * weights["consistency"],
        "recall": f_recall * weights["recall"],
        "runway": f_runway * weights["runway"],
    }
    total = sum(contributions.values())
    score = round(total * 100)

    # Find weakest factor for the recommendation line.
    normalised = {
        "mastery": f_mastery,
        "coverage": f_coverage,
        "mock": f_mock,
        "consistency": f_consistency,
        "recall": f_recall,
        "runway": f_runway,
    }
    weakest = min(normalised.items(), key=lambda kv: kv[1])
    recos = {
        "mastery": "Average mastery is the bottleneck — drill weak topics today.",
        "coverage": f"Coverage is only {round(coverage_pct*100)}% — start a new topic.",
        "mock": f"Mock accuracy is {round(recent_mock_accuracy_pct)}% — sit a full mock and debrief.",
        "consistency": f"Daily minutes averaging {round(avg_minutes_14d)} — protect a 90-min focus block.",
        "recall": "Recall queue is heavy — clear today's due cards before MCQs.",
        "runway": "Behind schedule — extend daily minutes or trim revision rounds.",
    }

    return {
        "score": score,
        "band": _band(score),
        "factors": [
            {
                "name": k,
                "value": round(v, 3),
                "weight": weights[k],
                "contribution": round(contributions[k] * 100, 1),
            }
            for k, v in normalised.items()
        ],
        "weakest_factor": weakest[0],
        "recommendation": recos.get(weakest[0], "Stay the course."),
        "computed_at": date.today().isoformat(),
    }


def what_if(
    base: Dict[str, Any],
    deltas: Dict[str, float],
) -> Dict[str, Any]:
    """
    Sliders the user can drag in the UI: 'what if I study 30 more min/day?'.
    `deltas` is a dict of factor → new value (already 0..1). Recomputes score.
    """
    factors = {f["name"]: f for f in base.get("factors", [])}
    new_vals = {}
    for name, info in factors.items():
        new_vals[name] = deltas.get(name, info["value"])
    weights = {f["name"]: f["weight"] for f in base.get("factors", [])}
    score = round(sum(new_vals[k] * weights[k] for k in new_vals) * 100)
    return {
        "score": score,
        "band": _band(score),
        "delta_vs_base": score - base.get("score", 0),
    }
