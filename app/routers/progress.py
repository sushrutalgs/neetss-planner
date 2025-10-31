from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

# ✅ FIXED IMPORTS
from app.database import get_db
from app import models
from app.schemas import ProgressUpdate
from app.auth import get_current_user

router = APIRouter(tags=["Progress"])

# ------------------------ GET PROGRESS ------------------------

@router.get("/plans/progress/{plan_id}")
def get_progress(
    plan_id: int,
    db: Session = Depends(get_db),
    current=Depends(get_current_user)
):
    """Get progress for a specific plan belonging to the logged-in user."""
    pr = (
        db.query(models.Progress)
        .filter(
            models.Progress.plan_id == plan_id,
            models.Progress.user_id == current.id
        )
        .first()
    )

    if not pr:
        raise HTTPException(status_code=404, detail="Progress not found")

    return {
        "plan_id": plan_id,
        "progress": pr.progress_json,
        "updated_at": pr.updated_at.isoformat(),
    }

# ------------------------ UPDATE PROGRESS ------------------------

@router.post("/plans/progress/{plan_id}")
def update_progress(
    plan_id: int,
    payload: ProgressUpdate,
    db: Session = Depends(get_db),
    current=Depends(get_current_user)
):
    """Update or overwrite study progress for a saved plan."""
    pr = (
        db.query(models.Progress)
        .filter(
            models.Progress.plan_id == plan_id,
            models.Progress.user_id == current.id
        )
        .first()
    )

    if not pr:
        raise HTTPException(status_code=404, detail="Progress not found")

    pr.progress_json = payload.progress
    db.add(pr)
    db.commit()
    db.refresh(pr)

    return {"ok": True, "updated_at": pr.updated_at.isoformat()}
