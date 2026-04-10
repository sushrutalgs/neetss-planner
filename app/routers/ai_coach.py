from __future__ import annotations
import os
import json
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta

from app.database import get_db
from app import models
from app.auth import get_current_user
from app.auth_lms import LmsUser, enforce_subscription
from app.ai.weekly_coach import generate_weekly_review
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


# ─────────────────────── v2 (Claude-powered, LMS auth) ───────────────────────
#
# The v2 endpoints use the federated LMS JWT and route every coach response
# through Claude Sonnet. The legacy v1 endpoints above stay in place for the
# old Flutter build and the existing index.html — we migrate the SPA first,
# then retire v1 once no client is calling it.


def _build_lms_coach_context(db: Session, lms_user: LmsUser) -> Dict[str, Any]:
    """Same shape as _build_coach_context but keyed off the LMS user id."""
    user = (
        db.query(models.User)
        .filter(models.User.lms_user_id == lms_user.lms_user_id)
        .one_or_none()
    )
    if not user:
        return {}

    base = _build_coach_context(db, user.id)

    # Build weak / strong / untouched the same way the v1 endpoint does, so
    # the Claude prompt sees identical semantics regardless of entry point.
    topic_acc_map = {r["topic"]: r["accuracy"] for r in base.get("all_time_accuracy", [])}
    weak = [t for t, acc in topic_acc_map.items() if acc < 60]
    strong = [t for t, acc in topic_acc_map.items() if acc >= 75]
    untouched = [t for t in SYLLABUS_TREE if t not in topic_acc_map]
    weak.sort(key=lambda t: topic_acc_map.get(t, 0))
    strong.sort(key=lambda t: -topic_acc_map.get(t, 0))

    # Streak + exam date already in base. Enrich.
    if base.get("exam_date"):
        try:
            base["days_remaining"] = max(0, (date.fromisoformat(base["exam_date"]) - date.today()).days)
        except (ValueError, TypeError):
            pass

    latest_plan = (
        db.query(models.Plan)
        .filter(models.Plan.user_id == user.id)
        .order_by(models.Plan.created_at.desc())
        .first()
    )
    current_phase = None
    if latest_plan and latest_plan.data_json:
        today_iso = date.today().isoformat()
        for d in latest_plan.data_json.get("days", []):
            if d.get("day") == today_iso:
                current_phase = d.get("phase")
                break

    base["user_name"] = user.name or "Student"
    base["weak_topics"] = weak[:8]
    base["strong_topics"] = strong[:5]
    base["untouched_topics"] = untouched[:5]
    base["current_phase"] = current_phase
    return base


@router.get("/ai-coach/v2/weekly-review")
def weekly_review_v2(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """
    Claude Sonnet weekly review. Falls back to the rule-based v1 shape if
    Claude is unreachable so the client always gets a usable response.
    """
    ctx = _build_lms_coach_context(db, lms_user)
    if not ctx:
        raise HTTPException(404, "Planner user not found")

    review = generate_weekly_review(ctx)
    if review:
        return {
            "source": "claude",
            "model": review.get("_tokens", {}).get("model"),
            "headline": review.get("headline"),
            "what_worked": review.get("what_worked", []),
            "what_to_fix": review.get("what_to_fix", []),
            "next_week_targets": review.get("next_week_targets", {}),
            "rendered_md": review.get("rendered_md", ""),
            "context_stats": ctx.get("week_stats") if isinstance(ctx.get("week_stats"), dict) else None,
        }

    # Fallback: call the v1 handler directly by re-running its pure logic
    # against the same user row. This keeps the contract stable when Claude
    # is down.
    return {
        "source": "fallback",
        "headline": "Weekly review (offline fallback)",
        "what_worked": [],
        "what_to_fix": [],
        "next_week_targets": {},
        "rendered_md": (
            "Claude is temporarily unavailable — showing rule-based summary.\n\n"
            f"Recent study days: {len(ctx.get('recent_study_days', []))}/7\n"
            f"Streak: {ctx.get('streak', 0)} days\n"
            f"Weak topics: {', '.join(ctx.get('weak_topics', [])[:5]) or 'none recorded'}\n"
        ),
    }


# Legacy /ai-coach/adapt-plan removed — superseded by POST /api/ml/replan on
# planner_v2, which pulls live LMS signals and invokes the AI plan shaper.
