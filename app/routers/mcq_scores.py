from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import date, timedelta

from app.database import get_db
from app import models
from app.schemas import MCQScoreCreate, MCQScoreOut
from app.auth import get_current_user

router = APIRouter(tags=["MCQ Scores"])


@router.post("/mcq-scores", response_model=MCQScoreOut)
def log_mcq_score(payload: MCQScoreCreate, db: Session = Depends(get_db), current=Depends(get_current_user)):
    if payload.correct > payload.attempted:
        raise HTTPException(status_code=400, detail="correct cannot exceed attempted")
    score = models.MCQScore(
        user_id=current.id,
        plan_id=payload.plan_id,
        date=payload.date,
        topic=payload.topic,
        subtopic=payload.subtopic,
        attempted=payload.attempted,
        correct=payload.correct,
        time_minutes=payload.time_minutes,
        source=payload.source,
        notes=payload.notes,
    )
    db.add(score)
    db.commit()
    db.refresh(score)
    accuracy = round(score.correct / max(1, score.attempted) * 100, 1)
    return MCQScoreOut(
        id=score.id, date=score.date.isoformat(), topic=score.topic,
        subtopic=score.subtopic, attempted=score.attempted, correct=score.correct,
        accuracy=accuracy, time_minutes=score.time_minutes,
        source=score.source, notes=score.notes,
    )


@router.get("/mcq-scores")
def list_mcq_scores(
    days: int = Query(30, ge=1, le=365),
    topic: Optional[str] = None,
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    since = date.today() - timedelta(days=days)
    q = db.query(models.MCQScore).filter(
        models.MCQScore.user_id == current.id,
        models.MCQScore.date >= since,
    )
    if topic:
        q = q.filter(models.MCQScore.topic == topic)
    scores = q.order_by(models.MCQScore.date.desc()).all()
    return [
        {
            "id": s.id, "date": s.date.isoformat(), "topic": s.topic,
            "subtopic": s.subtopic, "attempted": s.attempted, "correct": s.correct,
            "accuracy": round(s.correct / max(1, s.attempted) * 100, 1),
            "time_minutes": s.time_minutes, "source": s.source, "notes": s.notes,
        }
        for s in scores
    ]


@router.get("/mcq-scores/topic-summary")
def topic_accuracy_summary(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Get accuracy breakdown by topic."""
    results = (
        db.query(
            models.MCQScore.topic,
            func.sum(models.MCQScore.attempted).label("total_attempted"),
            func.sum(models.MCQScore.correct).label("total_correct"),
            func.count(models.MCQScore.id).label("sessions"),
        )
        .filter(models.MCQScore.user_id == current.id)
        .group_by(models.MCQScore.topic)
        .all()
    )
    return [
        {
            "topic": r.topic,
            "attempted": r.total_attempted or 0,
            "correct": r.total_correct or 0,
            "accuracy": round((r.total_correct or 0) / max(1, r.total_attempted or 1) * 100, 1),
            "sessions": r.sessions,
        }
        for r in results
    ]
