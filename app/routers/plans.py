from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Response, Query
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app import models
from app.schemas import PlanRequest, PlanResponse, SavePlanRequest, PlanListItem
from app.auth import get_current_user
from app.planner import build_schedule
from app.utils import plan_to_ics, plan_to_pdf
from app.priorities import SYLLABUS_TREE, get_subtopics

router = APIRouter(tags=["Plans"])


@router.post("/plan", response_model=PlanResponse)
def generate_plan(req: PlanRequest, current=Depends(get_current_user)):
    if req.exam_date <= req.start_date:
        raise HTTPException(status_code=400, detail="exam_date must be after start_date.")
    try:
        plan = build_schedule(
            start_date=req.start_date,
            exam_date=req.exam_date,
            hours_per_day=req.hours_per_day,
            mocks=req.mocks,
            avg_mcq_minutes=req.avg_minutes_per_mcq,
            rest_per_week=req.rest_per_week,
            custom_rest_dates=req.custom_rest_dates,
            custom_weights=req.custom_weights,
            selected_topics=req.selected_topics,
            revision_rounds=req.revision_rounds,
        )
        return plan
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plan generation failed: {e}")


@router.post("/plans/save", response_model=PlanListItem)
def save_plan(payload: SavePlanRequest, db: Session = Depends(get_db), current=Depends(get_current_user)):
    new_plan = models.Plan(
        user_id=current.id,
        name=payload.name or "My NEET SS Plan",
        data_json=payload.data,
        config_json=payload.config,
    )
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)
    if not db.query(models.Progress).filter(models.Progress.plan_id == new_plan.id).first():
        progress = models.Progress(user_id=current.id, plan_id=new_plan.id, progress_json={})
        db.add(progress)
        db.commit()
    return PlanListItem(id=new_plan.id, name=new_plan.name, created_at=new_plan.created_at.isoformat())


@router.get("/plans", response_model=List[PlanListItem])
def list_plans(db: Session = Depends(get_db), current=Depends(get_current_user)):
    plans = db.query(models.Plan).filter(models.Plan.user_id == current.id).order_by(models.Plan.created_at.desc()).all()
    return [PlanListItem(id=p.id, name=p.name, created_at=p.created_at.isoformat()) for p in plans]


@router.get("/plans/{plan_id}", response_model=PlanResponse)
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
    db.delete(plan)
    db.commit()
    return {"deleted": True, "id": plan_id}


@router.get("/plans/download/{plan_id}")
def download_plan(
    plan_id: int,
    type: str = Query("pdf", pattern="^(ics|pdf)$"),
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id, models.Plan.user_id == current.id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    try:
        if type == "ics":
            content = plan_to_ics(plan.data_json)
            return Response(content, media_type="text/calendar", headers={"Content-Disposition": f'attachment; filename="NEETSS_Plan_{plan_id}.ics"'})
        else:
            content = plan_to_pdf(plan.data_json)
            return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="NEETSS_Plan_{plan_id}.pdf"'})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export plan: {e}")


@router.get("/syllabus")
def get_syllabus():
    """Return full syllabus tree with subtopics and textbook refs."""
    result = {}
    for topic, data in SYLLABUS_TREE.items():
        result[topic] = {
            "priority": data["priority"],
            "weight": data["weight"],
            "subtopics": data["subtopics"],
        }
    return result
