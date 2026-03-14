from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date

from app.database import get_db
from app import models
from app.schemas import DailyNoteUpdate, DailyNoteOut
from app.auth import get_current_user

router = APIRouter(tags=["Notes"])


@router.post("/notes")
def upsert_note(payload: DailyNoteUpdate, db: Session = Depends(get_db), current=Depends(get_current_user)):
    existing = (
        db.query(models.DailyNote)
        .filter(models.DailyNote.user_id == current.id, models.DailyNote.plan_id == payload.plan_id, models.DailyNote.date == payload.date)
        .first()
    )
    if existing:
        existing.note = payload.note
        db.commit()
        db.refresh(existing)
        return {"date": existing.date.isoformat(), "note": existing.note, "updated_at": existing.updated_at.isoformat()}
    note = models.DailyNote(user_id=current.id, plan_id=payload.plan_id, date=payload.date, note=payload.note)
    db.add(note)
    db.commit()
    db.refresh(note)
    return {"date": note.date.isoformat(), "note": note.note, "updated_at": note.updated_at.isoformat()}


@router.get("/notes/{plan_id}")
def get_notes(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    notes = (
        db.query(models.DailyNote)
        .filter(models.DailyNote.user_id == current.id, models.DailyNote.plan_id == plan_id)
        .order_by(models.DailyNote.date)
        .all()
    )
    return {n.date.isoformat(): n.note for n in notes}
