from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta

from app.database import get_db
from app import models
from app.auth import get_current_user
from app.priorities import SYLLABUS_TREE

router = APIRouter(tags=["Leaderboard"])


@router.get("/leaderboard")
def get_leaderboard(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Anonymous leaderboard for opted-in users."""
    today = date.today()

    # Get all opted-in users
    users = db.query(models.User).filter(models.User.leaderboard_opt_in == True).all()
    entries = []

    for user in users:
        # Streak
        streak = 0
        check_date = today
        for _ in range(365):
            has = db.query(models.StudySession).filter(
                models.StudySession.user_id == user.id, models.StudySession.date == check_date
            ).first()
            if has:
                streak += 1
                check_date -= timedelta(days=1)
            else:
                break

        # Accuracy
        mcq = db.query(
            func.sum(models.MCQScore.attempted).label("att"),
            func.sum(models.MCQScore.correct).label("cor"),
        ).filter(models.MCQScore.user_id == user.id).first()
        att = mcq.att or 0
        cor = mcq.cor or 0
        accuracy = round(cor / max(1, att) * 100, 1)

        # Coverage
        topics_done = db.query(func.count(func.distinct(models.MCQScore.topic))).filter(
            models.MCQScore.user_id == user.id
        ).scalar() or 0
        coverage = round(topics_done / max(1, len(SYLLABUS_TREE)) * 100, 1)

        # Study hours (last 30 days)
        mins = db.query(func.sum(models.StudySession.duration_minutes)).filter(
            models.StudySession.user_id == user.id,
            models.StudySession.date >= today - timedelta(days=30),
        ).scalar() or 0

        # Anonymize name: "Dr. R****" pattern
        name = user.name or "Anonymous"
        if len(name) > 4:
            anon = f"Dr. {name.split()[0][0]}{'*' * 3}"
        else:
            anon = f"Dr. {name[0]}***"

        entries.append({
            "name": anon,
            "streak": streak,
            "accuracy": accuracy,
            "coverage": coverage,
            "study_hours": round(mins / 60, 1),
            "is_you": user.id == current.id,
        })

    # Sort by composite score
    for e in entries:
        e["score"] = round(e["streak"] * 0.3 + e["accuracy"] * 0.3 + e["coverage"] * 0.2 + e["study_hours"] * 0.2, 1)
    entries.sort(key=lambda x: -x["score"])

    for i, e in enumerate(entries):
        e["rank"] = i + 1

    return {"entries": entries, "total_participants": len(entries)}
