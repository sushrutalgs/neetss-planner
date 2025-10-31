from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..schemas import ProgressUpdate
from ..auth import get_current_user

router = APIRouter(tags=["progress"])

@router.get("/plans/progress/{plan_id}")
def get_progress(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    pr = db.query(models.Progress).filter(models.Progress.plan_id == plan_id, models.Progress.user_id == current.id).first()
    if not pr:
        raise HTTPException(status_code=404, detail="Progress not found")
    return {"plan_id": plan_id, "progress": pr.progress_json, "updated_at": pr.updated_at.isoformat()}

@router.post("/plans/progress/{plan_id}")
def update_progress(plan_id: int, payload: ProgressUpdate, db: Session = Depends(get_db), current=Depends(get_current_user)):
    pr = db.query(models.Progress).filter(models.Progress.plan_id == plan_id, models.Progress.user_id == current.id).first()
    if not pr:
        raise HTTPException(status_code=404, detail="Progress not found")
    pr.progress_json = payload.progress
    db.add(pr); db.commit(); db.refresh(pr)
    return {"ok": True, "updated_at": pr.updated_at.isoformat()}

