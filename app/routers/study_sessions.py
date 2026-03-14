from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta

from app.database import get_db
from app import models
from app.schemas import StudySessionCreate, StudySessionOut
from app.auth import get_current_user

router = APIRouter(tags=["Study Sessions"])


@router.post("/study-sessions", response_model=StudySessionOut)
def log_session(payload: StudySessionCreate, db: Session = Depends(get_db), current=Depends(get_current_user)):
    session = models.StudySession(
        user_id=current.id,
        plan_id=payload.plan_id,
        date=payload.date,
        topic=payload.topic,
        session_type=payload.session_type,
        duration_minutes=payload.duration_minutes,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return StudySessionOut(
        id=session.id, date=session.date.isoformat(), topic=session.topic,
        session_type=session.session_type, duration_minutes=session.duration_minutes,
        created_at=session.created_at.isoformat(),
    )


@router.get("/study-sessions")
def list_sessions(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    since = date.today() - timedelta(days=days)
    sessions = (
        db.query(models.StudySession)
        .filter(models.StudySession.user_id == current.id, models.StudySession.date >= since)
        .order_by(models.StudySession.date.desc())
        .all()
    )
    return [
        {
            "id": s.id, "date": s.date.isoformat(), "topic": s.topic,
            "session_type": s.session_type, "duration_minutes": s.duration_minutes,
        }
        for s in sessions
    ]


@router.get("/study-sessions/daily-totals")
def daily_study_totals(
    days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    """Return daily total study minutes for heatmap."""
    since = date.today() - timedelta(days=days)
    results = (
        db.query(
            models.StudySession.date,
            func.sum(models.StudySession.duration_minutes).label("total_minutes"),
            func.count(models.StudySession.id).label("sessions"),
        )
        .filter(models.StudySession.user_id == current.id, models.StudySession.date >= since)
        .group_by(models.StudySession.date)
        .order_by(models.StudySession.date)
        .all()
    )
    return [
        {"date": r.date.isoformat(), "minutes": round(r.total_minutes or 0, 1), "sessions": r.sessions}
        for r in results
    ]
