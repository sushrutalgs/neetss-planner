from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta

from app.database import get_db
from app import models
from app.auth import get_current_user
from app.priorities import SYLLABUS_TREE, predict_score_range

router = APIRouter(tags=["Dashboard"])


@router.get("/dashboard")
def dashboard_data(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Single endpoint returning all data needed for the dashboard view."""
    today = date.today()
    today_iso = today.isoformat()

    # ── 1. Core stats ──
    total_minutes = (
        db.query(func.sum(models.StudySession.duration_minutes))
        .filter(models.StudySession.user_id == current.id)
        .scalar() or 0
    )
    total_hours = round(total_minutes / 60, 1)

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

    # ── 2. Streak ──
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

    # ── 3. Today's study minutes ──
    today_minutes = (
        db.query(func.sum(models.StudySession.duration_minutes))
        .filter(models.StudySession.user_id == current.id, models.StudySession.date == today)
        .scalar() or 0
    )
    today_hours = round(today_minutes / 60, 1)

    # ── 4. Today's MCQs ──
    today_mcq = (
        db.query(
            func.sum(models.MCQScore.attempted).label("att"),
            func.sum(models.MCQScore.correct).label("cor"),
        )
        .filter(models.MCQScore.user_id == current.id, models.MCQScore.date == today)
        .first()
    )
    today_mcqs_attempted = today_mcq.att or 0
    today_mcqs_correct = today_mcq.cor or 0

    # ── 5. Recall cards due ──
    recall_due = (
        db.query(func.count(models.RecallCard.id))
        .filter(models.RecallCard.user_id == current.id, models.RecallCard.next_review_date <= today)
        .scalar() or 0
    )
    recall_total = (
        db.query(func.count(models.RecallCard.id))
        .filter(models.RecallCard.user_id == current.id)
        .scalar() or 0
    )

    # ── 6. Topics coverage ──
    topics_with_data = (
        db.query(models.MCQScore.topic)
        .filter(models.MCQScore.user_id == current.id)
        .distinct()
        .count()
    )
    topics_total = len(SYLLABUS_TREE)

    # ── 7. Weekly hours (last 8 weeks for sparkline) ──
    weekly_hours = []
    for w in range(8):
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

    # ── 8. Last 7 days activity ──
    daily_activity = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        mins = (
            db.query(func.sum(models.StudySession.duration_minutes))
            .filter(models.StudySession.user_id == current.id, models.StudySession.date == d)
            .scalar() or 0
        )
        mcqs = (
            db.query(func.sum(models.MCQScore.attempted))
            .filter(models.MCQScore.user_id == current.id, models.MCQScore.date == d)
            .scalar() or 0
        )
        daily_activity.append({
            "date": d.isoformat(),
            "day": d.strftime("%a"),
            "minutes": round(mins or 0),
            "mcqs": mcqs or 0,
        })

    # ── 9. Heatmap (last 90 days) ──
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
    heatmap = {r.date.isoformat(): round(r.mins or 0, 1) for r in heatmap_data}

    # ── 10. Top 5 weakest topics ──
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
    weakness = {}
    for r in topic_results:
        acc = (r.cor or 0) / max(1, r.att or 1)
        weakness[r.topic] = {
            "accuracy": round(acc * 100, 1),
            "attempted": r.att or 0,
            "priority": SYLLABUS_TREE.get(r.topic, {}).get("priority", "P3_SUPPORT"),
        }
    weakest = sorted(weakness.items(), key=lambda x: x[1]["accuracy"])[:5]

    # ── 11. Latest plan meta ──
    latest_plan = (
        db.query(models.Plan)
        .filter(models.Plan.user_id == current.id)
        .order_by(models.Plan.created_at.desc())
        .first()
    )
    plan_meta = None
    days_remaining = 0
    total_plan_days = 180
    if latest_plan and latest_plan.data_json and "meta" in latest_plan.data_json:
        meta = latest_plan.data_json["meta"]
        try:
            exam = date.fromisoformat(meta.get("exam_date", ""))
            days_remaining = max(0, (exam - today).days)
            total_plan_days = meta.get("days", 180)
        except (ValueError, TypeError):
            pass
        plan_meta = {
            "plan_id": latest_plan.id,
            "plan_name": latest_plan.name,
            "exam_date": meta.get("exam_date"),
            "start_date": meta.get("start_date"),
            "days_remaining": days_remaining,
            "total_days": total_plan_days,
            "hours_per_day": meta.get("hours_per_day", 6),
        }

    # ── 12. Predicted score ──
    p1_topics = [r for r in topic_results if SYLLABUS_TREE.get(r.topic, {}).get("priority") == "P1_HIGH"]
    p1_correct = sum(r.cor or 0 for r in p1_topics)
    p1_attempted = sum(r.att or 0 for r in p1_topics)
    p1_accuracy = p1_correct / max(1, p1_attempted)
    coverage_pct = topics_with_data / max(1, topics_total)
    predicted = predict_score_range(
        coverage_pct=coverage_pct,
        avg_accuracy=overall_accuracy / 100,
        p1_accuracy=p1_accuracy,
        days_remaining=days_remaining,
        total_days=total_plan_days,
    )

    # ── 13. Today's schedule from plan ──
    today_schedule = None
    if latest_plan and latest_plan.data_json and "schedule" in latest_plan.data_json:
        for day_data in latest_plan.data_json["schedule"]:
            if day_data.get("date") == today_iso:
                today_schedule = day_data
                break

    return {
        "user": {"name": current.name, "goal": current.goal, "exam_type": current.exam_type},
        "stats": {
            "total_study_hours": total_hours,
            "total_mcqs": total_attempted,
            "total_correct": total_correct,
            "overall_accuracy": overall_accuracy,
            "streak_days": streak,
            "topics_started": topics_with_data,
            "topics_total": topics_total,
        },
        "today": {
            "hours": today_hours,
            "mcqs_attempted": today_mcqs_attempted,
            "mcqs_correct": today_mcqs_correct,
            "schedule": today_schedule,
        },
        "recall": {
            "due": recall_due,
            "total": recall_total,
        },
        "daily_activity": daily_activity,
        "weekly_hours": weekly_hours,
        "heatmap": heatmap,
        "weakest_topics": [{"topic": t, **d} for t, d in weakest],
        "plan_meta": plan_meta,
        "predicted_score": predicted,
    }
