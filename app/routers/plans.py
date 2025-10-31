from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlalchemy.orm import Session
from typing import List

from ..database import get_db
from .. import models
from ..schemas import PlanRequest, PlanResponse, SavePlanRequest, PlanListItem
from ..auth import get_current_user
from ..planner import build_schedule
from ..utils import plan_to_ics, plan_to_pdf

router = APIRouter(tags=["plans"])

@router.post("/plan", response_model=PlanResponse)
def generate_plan(req: PlanRequest, current=Depends(get_current_user)):
    if req.exam_date <= req.start_date:
        raise HTTPException(status_code=400, detail="exam_date must be after start_date.")
    plan = build_schedule(
        start_date=req.start_date,
        exam_date=req.exam_date,
        hours_per_day=req.hours_per_day,
        mocks=req.mocks,
        avg_mcq_minutes=req.avg_minutes_per_mcq,
        plan_type=req.plan_type,
    )
    return plan

@router.post("/plans/save", response_model=PlanListItem)
def save_plan(payload: SavePlanRequest, db: Session = Depends(get_db), current=Depends(get_current_user)):
    p = models.Plan(user_id=current.id, name=payload.name, data_json=payload.data)
    db.add(p); db.commit(); db.refresh(p)
    # create empty progress row if not exists
    if not db.query(models.Progress).filter(models.Progress.plan_id == p.id).first():
        pr = models.Progress(user_id=current.id, plan_id=p.id, progress_json={})
        db.add(pr); db.commit()
    return PlanListItem(id=p.id, name=p.name, created_at=p.created_at.isoformat())

@router.get("/plans/list", response_model=List[PlanListItem])
def list_plans(db: Session = Depends(get_db), current=Depends(get_current_user)):
    items = db.query(models.Plan).filter(models.Plan.user_id == current.id).order_by(models.Plan.created_at.desc()).all()
    return [PlanListItem(id=i.id, name=i.name, created_at=i.created_at.isoformat()) for i in items]

@router.get("/plans/get/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id, models.Plan.user_id == current.id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan.data_json

@router.delete("/plans/delete/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id, models.Plan.user_id == current.id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    db.delete(plan); db.commit()
    return {"deleted": True}

@router.get("/plans/download/{plan_id}")
def download_plan(plan_id: int, type: str = Query("ics", regex="^(ics|pdf)$"), db: Session = Depends(get_db), current=Depends(get_current_user)):
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id, models.Plan.user_id == current.id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    if type == "ics":
        content = plan_to_ics(plan.data_json)
        return Response(content, media_type="text/calendar", headers={"Content-Disposition": f'attachment; filename="NEETSS_Plan_{plan_id}.ics"'})
    else:
        content = plan_to_pdf(plan.data_json)
        return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="NEETSS_Plan_{plan_id}.pdf"'})

