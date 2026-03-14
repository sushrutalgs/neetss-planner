from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta
from typing import Dict

from app.database import get_db
from app import models
from app.auth import get_current_user
from app.priorities import SYLLABUS_TREE, predict_score_range

router = APIRouter(tags=["Analytics"])


@router.get("/analytics/summary")
def analytics_summary(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Comprehensive analytics for the user's dashboard."""
    today = date.today()

    # Study hours
    total_minutes = (
        db.query(func.sum(models.StudySession.duration_minutes))
        .filter(models.StudySession.user_id == current.id)
        .scalar() or 0
    )
    total_hours = round(total_minutes / 60, 1)

    # MCQ stats
    mcq_agg = (
        db.query(
            func.sum(models.MCQScore.attempted).label("attempted"),
            func.sum(models.MCQScore.correct).label("correct"),
        )
        .filter(models.MCQScore.user_id == current.id)
        .first()
    )
    total_attempted = mcq_agg.attempted or 0
    total_correct = mcq_agg.correct or 0
    overall_accuracy = round(total_correct / max(1, total_attempted) * 100, 1)

    # Topic accuracy breakdown
    topic_results = (
        db.query(
            models.MCQScore.topic,
            func.sum(models.MCQScore.attempted).label("att"),
            func.sum(models.MCQScore.correct).label("cor"),
        )
        .filter(models.MCQScore.user_id == current.id)
        .group_by(models.MCQScore.topic)
        .all()
    )
    topic_accuracy = [
        {
            "topic": r.topic,
            "attempted": r.att or 0,
            "correct": r.cor or 0,
            "accuracy": round((r.cor or 0) / max(1, r.att or 1) * 100, 1),
            "priority": SYLLABUS_TREE.get(r.topic, {}).get("priority", "P3_SUPPORT"),
        }
        for r in topic_results
    ]

    # Streak calculation
    streak = 0
    check_date = today
    for _ in range(365):
        has_session = (
            db.query(models.StudySession)
            .filter(models.StudySession.user_id == current.id, models.StudySession.date == check_date)
            .first()
        )
        if has_session:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    # Weekly hours (last 12 weeks)
    weekly_hours = []
    for w in range(12):
        week_start = today - timedelta(days=today.weekday() + 7 * w)
        week_end = week_start + timedelta(days=6)
        mins = (
            db.query(func.sum(models.StudySession.duration_minutes))
            .filter(
                models.StudySession.user_id == current.id,
                models.StudySession.date >= week_start,
                models.StudySession.date <= week_end,
            )
            .scalar() or 0
        )
        weekly_hours.append({
            "week_start": week_start.isoformat(),
            "hours": round(mins / 60, 1),
        })
    weekly_hours.reverse()

    # Heatmap (last 90 days)
    heatmap_start = today - timedelta(days=90)
    heatmap_data = (
        db.query(
            models.StudySession.date,
            func.sum(models.StudySession.duration_minutes).label("mins"),
        )
        .filter(models.StudySession.user_id == current.id, models.StudySession.date >= heatmap_start)
        .group_by(models.StudySession.date)
        .all()
    )
    heatmap = [{"date": r.date.isoformat(), "minutes": round(r.mins or 0, 1)} for r in heatmap_data]

    # Topics started
    topics_with_data = set(r.topic for r in topic_results)
    topics_total = len(SYLLABUS_TREE)

    # Predicted score
    p1_topics = [r for r in topic_results if SYLLABUS_TREE.get(r.topic, {}).get("priority") == "P1_HIGH"]
    p1_correct = sum(r.cor or 0 for r in p1_topics)
    p1_attempted = sum(r.att or 0 for r in p1_topics)
    p1_accuracy = p1_correct / max(1, p1_attempted)

    # Get plan for days remaining
    latest_plan = (
        db.query(models.Plan)
        .filter(models.Plan.user_id == current.id)
        .order_by(models.Plan.created_at.desc())
        .first()
    )
    days_remaining = 90
    total_plan_days = 180
    if latest_plan and latest_plan.data_json and "meta" in latest_plan.data_json:
        meta = latest_plan.data_json["meta"]
        try:
            exam = date.fromisoformat(meta.get("exam_date", ""))
            days_remaining = max(0, (exam - today).days)
            total_plan_days = meta.get("days", 180)
        except (ValueError, TypeError):
            pass

    coverage_pct = len(topics_with_data) / max(1, topics_total)
    predicted = predict_score_range(
        coverage_pct=coverage_pct,
        avg_accuracy=overall_accuracy / 100,
        p1_accuracy=p1_accuracy,
        days_remaining=days_remaining,
        total_days=total_plan_days,
    )

    return {
        "total_study_hours": total_hours,
        "total_mcqs_attempted": total_attempted,
        "total_mcqs_correct": total_correct,
        "overall_accuracy": overall_accuracy,
        "streak_days": streak,
        "topics_started": len(topics_with_data),
        "topics_total": topics_total,
        "weekly_hours": weekly_hours,
        "topic_accuracy": topic_accuracy,
        "heatmap": heatmap,
        "predicted_score": predicted,
    }


@router.get("/analytics/weakness")
def weakness_map(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Weakness heatmap: topic × accuracy for adaptive reweighting."""
    topic_results = (
        db.query(
            models.MCQScore.topic,
            func.sum(models.MCQScore.attempted).label("att"),
            func.sum(models.MCQScore.correct).label("cor"),
        )
        .filter(models.MCQScore.user_id == current.id)
        .group_by(models.MCQScore.topic)
        .all()
    )

    weakness: Dict[str, float] = {}
    for r in topic_results:
        acc = (r.cor or 0) / max(1, r.att or 1)
        # Weakness = inverse of accuracy (higher = weaker)
        weakness[r.topic] = round(max(0, 1.0 - acc), 3)

    # Include topics with no data as maximum weakness
    for topic in SYLLABUS_TREE:
        if topic not in weakness:
            weakness[topic] = 1.0

    return {
        "weakness_map": weakness,
        "strongest": sorted(weakness.items(), key=lambda x: x[1])[:5],
        "weakest": sorted(weakness.items(), key=lambda x: -x[1])[:5],
    }
