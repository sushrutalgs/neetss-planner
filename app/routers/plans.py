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

router = APIRouter(tags=["Plans"])

# ------------------------ GENERATE PLAN ------------------------

@router.post("/plan", response_model=PlanResponse)
def generate_plan(req: PlanRequest, current=Depends(get_current_user)):
    """Generate a new study plan dynamically using build_schedule()."""
    if req.exam_date <= req.start_date:
        raise HTTPException(status_code=400, detail="exam_date must be after start_date.")

    # Build the complete plan
    plan = build_schedule(
        start_date=req.start_date,
        exam_date=req.exam_date,
        hours_per_day=req.hours_per_day,
        mocks=req.mocks,
        avg_mcq_minutes=req.avg_minutes_per_mcq,
        plan_type=req.plan_type,
    )

    # ---------------- FILTER PLAN CONTENT BY TYPE ---------------- #
    plan_type = (req.plan_type or "full").lower()
    if plan_type != "full":
        schedule = plan.get("schedule", [])
        filtered_schedule = []

        for day in schedule:
            d = day.copy()

            if plan_type == "theory":
                d["mcq"], d["recall"] = None, None
                if not d.get("is_mock_day"):
                    d["mock"] = None

            elif plan_type == "mcq":
                d["theory"], d["recall"] = None, None
                if not d.get("is_mock_day"):
                    d["mock"] = None

            elif plan_type == "revision":
                d["theory"], d["mcq"] = None, None
                if not d.get("is_mock_day"):
                    d["mock"] = None

            elif plan_type == "mock":
                if not d.get("is_mock_day"):
                    continue
                d["theory"], d["mcq"], d["recall"] = None, None, None

            filtered_schedule.append(d)

        plan["schedule"] = filtered_schedule

        # Adjust weekly summaries
        for w in plan.get("weekly_summaries", []):
            t = w.get("weekly_targets", {})
            if plan_type == "theory":
                t["approx_mcqs"] = 0
                t["recall_min"] = 0
            elif plan_type == "mcq":
                t["theory_min"] = 0
                t["recall_min"] = 0
            elif plan_type == "revision":
                t["theory_min"] = 0
                t["approx_mcqs"] = 0
            elif plan_type == "mock":
                t["theory_min"] = 0
                t["approx_mcqs"] = 0
                t["recall_min"] = 0

    return plan


# ------------------------ SAVE PLAN ------------------------

@router.post("/plans/save", response_model=PlanListItem)
def save_plan(payload: SavePlanRequest, db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Save a generated plan for the current user."""
    p = models.Plan(user_id=current.id, name=payload.name, data_json=payload.data)
    db.add(p)
    db.commit()
    db.refresh(p)

    if not db.query(models.Progress).filter(models.Progress.plan_id == p.id).first():
        pr = models.Progress(user_id=current.id, plan_id=p.id, progress_json={})
        db.add(pr)
        db.commit()

    return PlanListItem(id=p.id, name=p.name, created_at=p.created_at.isoformat())


# ------------------------ LIST SAVED PLANS ------------------------

@router.get("/plans/list", response_model=List[PlanListItem])
def list_plans(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """List all saved plans for the current user."""
    plans = (
        db.query(models.Plan)
        .filter(models.Plan.user_id == current.id)
        .order_by(models.Plan.created_at.desc())
        .all()
    )
    return [
        PlanListItem(id=p.id, name=p.name, created_at=p.created_at.isoformat())
        for p in plans
    ]


# ------------------------ GET SPECIFIC PLAN ------------------------

@router.get("/plans/get/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Retrieve a saved plan by its ID."""
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id, models.Plan.user_id == current.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan.data_json


# ------------------------ DELETE PLAN ------------------------

@router.delete("/plans/delete/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Delete a saved plan by its ID."""
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id, models.Plan.user_id == current.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    db.delete(plan)
    db.commit()
    return {"deleted": True}


# ------------------------ DOWNLOAD PLAN ------------------------

@router.get("/plans/download/{plan_id}")
def download_plan(
    plan_id: int,
    type: str = Query("ics", pattern="^(ics|pdf)$"),
    db: Session = Depends(get_db),
    current=Depends(get_current_user)
):
    """Download a saved plan as ICS (Google Calendar) or PDF."""
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id, models.Plan.user_id == current.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    if type == "ics":
        content = plan_to_ics(plan.data_json)
        media_type = "text/calendar"
        ext = "ics"
    else:
        content = plan_to_pdf(plan.data_json)
        media_type = "application/pdf"
        ext = "pdf"

    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="NEETSS_Plan_{plan_id}.{ext}"'}
    )
