from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date, timedelta

from app.database import get_db
from app import models
from app.schemas import RecallReview
from app.auth import get_current_user
from app.priorities import sm2_next_interval

router = APIRouter(tags=["Recall"])


@router.get("/recall/due")
def get_due_recalls(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Get all recall cards due today or overdue."""
    today = date.today()
    cards = (
        db.query(models.RecallCard)
        .filter(models.RecallCard.user_id == current.id, models.RecallCard.next_review_date <= today)
        .order_by(models.RecallCard.next_review_date)
        .all()
    )
    return [
        {
            "id": c.id, "topic": c.topic, "subtopic": c.subtopic,
            "ease_factor": round(c.ease_factor, 2), "interval_days": c.interval_days,
            "repetitions": c.repetitions, "next_review_date": c.next_review_date.isoformat(),
            "last_quality": c.last_quality, "overdue_days": (today - c.next_review_date).days,
        }
        for c in cards
    ]


@router.post("/recall/review")
def review_card(payload: RecallReview, db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Review a recall card with SM-2 quality rating."""
    card = (
        db.query(models.RecallCard)
        .filter(
            models.RecallCard.user_id == current.id,
            models.RecallCard.topic == payload.topic,
            models.RecallCard.subtopic == payload.subtopic,
        )
        .first()
    )
    today = date.today()

    if not card:
        # Create new card
        interval, ef, reps = sm2_next_interval(payload.quality, 0, 1, 2.5)
        card = models.RecallCard(
            user_id=current.id,
            topic=payload.topic,
            subtopic=payload.subtopic,
            ease_factor=ef,
            interval_days=interval,
            repetitions=reps,
            next_review_date=today + timedelta(days=interval),
            last_quality=payload.quality,
        )
        db.add(card)
    else:
        interval, ef, reps = sm2_next_interval(
            payload.quality, card.repetitions, card.interval_days, card.ease_factor,
        )
        card.ease_factor = ef
        card.interval_days = interval
        card.repetitions = reps
        card.next_review_date = today + timedelta(days=interval)
        card.last_quality = payload.quality

    db.commit()
    db.refresh(card)
    return {
        "topic": card.topic, "subtopic": card.subtopic,
        "next_review": card.next_review_date.isoformat(),
        "interval_days": card.interval_days,
        "ease_factor": round(card.ease_factor, 2),
    }


@router.get("/recall/stats")
def recall_stats(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Summary of recall card states."""
    today = date.today()
    all_cards = db.query(models.RecallCard).filter(models.RecallCard.user_id == current.id).all()
    due = sum(1 for c in all_cards if c.next_review_date <= today)
    mature = sum(1 for c in all_cards if c.interval_days >= 21)
    young = sum(1 for c in all_cards if c.interval_days < 21)
    return {
        "total_cards": len(all_cards),
        "due_today": due,
        "mature": mature,
        "young": young,
        "avg_ease": round(sum(c.ease_factor for c in all_cards) / max(1, len(all_cards)), 2),
    }
