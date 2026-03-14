from __future__ import annotations
import os
import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta

from app.database import get_db
from app import models
from app.auth import get_current_user
from app.priorities import SYLLABUS_TREE

router = APIRouter(tags=["AI Coach"])


def _build_coach_context(db: Session, user_id: int) -> dict:
    """Build context for AI coach analysis."""
    today = date.today()
    week_ago = today - timedelta(days=7)

    # Recent MCQ performance
    recent_mcqs = (
        db.query(
            models.MCQScore.topic,
            func.sum(models.MCQScore.attempted).label("att"),
            func.sum(models.MCQScore.correct).label("cor"),
        )
        .filter(models.MCQScore.user_id == user_id, models.MCQScore.date >= week_ago)
        .group_by(models.MCQScore.topic)
        .all()
    )

    # Recent study hours
    recent_hours = (
        db.query(
            models.StudySession.date,
            func.sum(models.StudySession.duration_minutes).label("mins"),
        )
        .filter(models.StudySession.user_id == user_id, models.StudySession.date >= week_ago)
        .group_by(models.StudySession.date)
        .all()
    )

    # All-time topic accuracy
    all_mcqs = (
        db.query(
            models.MCQScore.topic,
            func.sum(models.MCQScore.attempted).label("att"),
            func.sum(models.MCQScore.correct).label("cor"),
        )
        .filter(models.MCQScore.user_id == user_id)
        .group_by(models.MCQScore.topic)
        .all()
    )

    # Latest plan
    latest_plan = (
        db.query(models.Plan)
        .filter(models.Plan.user_id == user_id)
        .order_by(models.Plan.created_at.desc())
        .first()
    )

    # Streak
    streak = 0
    check = today
    for _ in range(365):
        has = db.query(models.StudySession).filter(
            models.StudySession.user_id == user_id, models.StudySession.date == check
        ).first()
        if has:
            streak += 1
            check -= timedelta(days=1)
        else:
            break

    return {
        "recent_mcqs": [
            {"topic": r.topic, "attempted": r.att or 0, "correct": r.cor or 0,
             "accuracy": round((r.cor or 0) / max(1, r.att or 1) * 100, 1)}
            for r in recent_mcqs
        ],
        "recent_study_days": [
            {"date": r.date.isoformat(), "hours": round((r.mins or 0) / 60, 1)}
            for r in recent_hours
        ],
        "all_time_accuracy": [
            {"topic": r.topic, "attempted": r.att or 0, "correct": r.cor or 0,
             "accuracy": round((r.cor or 0) / max(1, r.att or 1) * 100, 1)}
            for r in all_mcqs
        ],
        "streak": streak,
        "exam_date": latest_plan.data_json.get("meta", {}).get("exam_date") if latest_plan and latest_plan.data_json else None,
        "days_remaining": None,
    }


@router.get("/ai-coach/weekly-review")
def weekly_review(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Generate AI-powered weekly review and recommendations."""
    ctx = _build_coach_context(db, current.id)

    # Calculate days remaining
    if ctx["exam_date"]:
        try:
            exam = date.fromisoformat(ctx["exam_date"])
            ctx["days_remaining"] = max(0, (exam - date.today()).days)
        except (ValueError, TypeError):
            pass

    # Identify weak and strong topics
    weak_topics = []
    strong_topics = []
    untouched_topics = []

    topic_acc_map = {r["topic"]: r["accuracy"] for r in ctx["all_time_accuracy"]}
    for topic in SYLLABUS_TREE:
        if topic in topic_acc_map:
            if topic_acc_map[topic] < 60:
                weak_topics.append({"topic": topic, "accuracy": topic_acc_map[topic]})
            elif topic_acc_map[topic] >= 75:
                strong_topics.append({"topic": topic, "accuracy": topic_acc_map[topic]})
        else:
            untouched_topics.append(topic)

    weak_topics.sort(key=lambda x: x["accuracy"])
    strong_topics.sort(key=lambda x: -x["accuracy"])

    # Weekly performance
    study_days = len(ctx["recent_study_days"])
    total_weekly_hours = sum(d["hours"] for d in ctx["recent_study_days"])
    weekly_mcqs = sum(r["attempted"] for r in ctx["recent_mcqs"])
    weekly_accuracy = (
        round(sum(r["correct"] for r in ctx["recent_mcqs"]) / max(1, weekly_mcqs) * 100, 1)
        if weekly_mcqs > 0 else 0
    )

    # Build recommendations
    recommendations = []

    if study_days < 5:
        recommendations.append(f"You studied only {study_days}/7 days this week. Consistency is key — aim for at least 6 days.")
    else:
        recommendations.append(f"Great consistency — {study_days}/7 days this week. Keep this momentum.")

    if weekly_mcqs < 50:
        recommendations.append(f"Only {weekly_mcqs} MCQs this week. Try to hit at least 100/week for exam readiness.")
    elif weekly_mcqs >= 150:
        recommendations.append(f"Excellent MCQ volume: {weekly_mcqs} this week. Make sure you're analyzing wrong answers too.")

    if weak_topics:
        top_weak = [w["topic"] for w in weak_topics[:3]]
        recommendations.append(f"Weakest areas: {', '.join(top_weak)}. Increase MCQ sessions for these topics.")

    if untouched_topics:
        recommendations.append(f"{len(untouched_topics)} topics not yet started. Begin with the P1 ones: {', '.join([t for t in untouched_topics if SYLLABUS_TREE[t]['priority'] == 'P1_HIGH'][:3])}.")

    if ctx["days_remaining"] is not None and ctx["days_remaining"] < 30:
        recommendations.append("Less than 30 days to exam — shift to rapid revision and mock-heavy schedule.")

    # Build analysis text
    analysis = (
        f"📊 This week: {study_days} study days, {total_weekly_hours:.1f} hours total, "
        f"{weekly_mcqs} MCQs at {weekly_accuracy}% accuracy. "
        f"Current streak: {ctx['streak']} days. "
    )
    if ctx["days_remaining"] is not None:
        analysis += f"\n⏳ {ctx['days_remaining']} days until exam."

    # Weakness boost map for adaptive plan regeneration
    weakness_boost = {}
    for w in weak_topics:
        # Boost factor: accuracy below 60% gets progressively more weight
        boost = max(0, (60 - w["accuracy"]) / 100) * 2
        weakness_boost[w["topic"]] = round(boost, 2)

    return {
        "analysis": analysis,
        "weak_topics": [w["topic"] for w in weak_topics[:5]],
        "strong_topics": [s["topic"] for s in strong_topics[:5]],
        "untouched_topics": untouched_topics[:5],
        "recommendations": recommendations,
        "stats": {
            "study_days": study_days,
            "total_hours": round(total_weekly_hours, 1),
            "mcqs_done": weekly_mcqs,
            "accuracy": weekly_accuracy,
            "streak": ctx["streak"],
            "days_remaining": ctx["days_remaining"],
        },
        "weakness_boost": weakness_boost,
    }


@router.post("/ai-coach/adapt-plan")
def adapt_plan(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Regenerate remaining schedule based on weakness data."""
    # Get weakness data
    all_mcqs = (
        db.query(
            models.MCQScore.topic,
            func.sum(models.MCQScore.attempted).label("att"),
            func.sum(models.MCQScore.correct).label("cor"),
        )
        .filter(models.MCQScore.user_id == current.id)
        .group_by(models.MCQScore.topic)
        .all()
    )

    weakness_boost = {}
    for r in all_mcqs:
        acc = (r.cor or 0) / max(1, r.att or 1)
        if acc < 0.6:
            weakness_boost[r.topic] = round(max(0, (0.6 - acc)) * 3, 2)

    # Get latest plan config
    latest_plan = (
        db.query(models.Plan)
        .filter(models.Plan.user_id == current.id)
        .order_by(models.Plan.created_at.desc())
        .first()
    )

    if not latest_plan or not latest_plan.data_json:
        raise HTTPException(status_code=404, detail="No plan found to adapt")

    meta = latest_plan.data_json.get("meta", {})
    today = date.today()
    exam_date_str = meta.get("exam_date")

    if not exam_date_str:
        raise HTTPException(status_code=400, detail="Plan has no exam date")

    try:
        exam_date = date.fromisoformat(exam_date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid exam date in plan")

    if exam_date <= today:
        raise HTTPException(status_code=400, detail="Exam date has already passed")

    # Regenerate from today
    from app.planner import build_schedule
    new_plan = build_schedule(
        start_date=today,
        exam_date=exam_date,
        hours_per_day=meta.get("hours_per_day", 6),
        mocks=len(meta.get("mock_days_indexed", [])),
        avg_mcq_minutes=2.5,
        rest_per_week=len(meta.get("rest_days_indexed", [])) // max(1, meta.get("days", 1) // 7),
        weakness_data=weakness_boost,
    )

    return {
        "adapted": True,
        "weakness_boost_applied": weakness_boost,
        "new_plan": new_plan,
    }
