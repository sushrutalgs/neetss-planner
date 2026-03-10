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


# ============================================================
#                     GENERATE FULL PLAN
# ============================================================

@router.post("/plan", response_model=PlanResponse)
def generate_plan(req: PlanRequest, current=Depends(get_current_user)):
    """
    Generate a new study plan dynamically using build_schedule().
    Includes Theory, MCQs, Recall, and Mocks.
    """
    if req.exam_date <= req.start_date:
        raise HTTPException(status_code=400, detail="exam_date must be after start_date.")

    try:
        plan = build_schedule(
            start_date=req.start_date,
            exam_date=req.exam_date,
            hours_per_day=req.hours_per_day,
            mocks=req.mocks,
            avg_mcq_minutes=req.avg_minutes_per_mcq,
        )
        return plan
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plan generation failed: {e}")


# ============================================================
#                     SAVE PLAN
# ============================================================

@router.post("/plans/save", response_model=PlanListItem)
def save_plan(
    payload: SavePlanRequest,
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    """
    Save a generated plan for the logged-in user.
    Automatically creates a linked progress record.
    """
    new_plan = models.Plan(
        user_id=current.id,
        name=payload.name or "My NEET SS Plan",
        data_json=payload.data,
    )
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)

    # Ensure progress record exists
    if not db.query(models.Progress).filter(models.Progress.plan_id == new_plan.id).first():
        progress = models.Progress(user_id=current.id, plan_id=new_plan.id, progress_json={})
        db.add(progress)
        db.commit()

    return PlanListItem(id=new_plan.id, name=new_plan.name, created_at=new_plan.created_at.isoformat())


# ============================================================
#                     LIST ALL SAVED PLANS
# ============================================================

@router.get("/plans", response_model=List[PlanListItem])
def list_plans(db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Retrieve all saved plans for the logged-in user (newest first)."""
    plans = (
        db.query(models.Plan)
        .filter(models.Plan.user_id == current.id)
        .order_by(models.Plan.created_at.desc())
        .all()
    )
    return [PlanListItem(id=p.id, name=p.name, created_at=p.created_at.isoformat()) for p in plans]


# ============================================================
#                     GET SPECIFIC PLAN
# ============================================================

@router.get("/plans/{plan_id}", response_model=PlanResponse)
def get_plan(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Fetch a saved plan by ID."""
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id, models.Plan.user_id == current.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan.data_json


# ============================================================
#                     DELETE PLAN
# ============================================================

@router.delete("/plans/delete/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), current=Depends(get_current_user)):
    """Delete a plan by ID."""
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id, models.Plan.user_id == current.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    db.delete(plan)
    db.commit()
    return {"deleted": True, "id": plan_id}


# ============================================================
#                     DOWNLOAD PLAN (ICS / PDF)
# ============================================================

@router.get("/plans/download/{plan_id}")
def download_plan(
    plan_id: int,
    type: str = Query("pdf", pattern="^(ics|pdf)$", description="Download format (ics or pdf)"),
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    """Download a saved plan as an ICS calendar or PDF summary file."""
    plan = (
        db.query(models.Plan)
        .filter(models.Plan.id == plan_id, models.Plan.user_id == current.id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    try:
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
            headers={"Content-Disposition": f'attachment; filename="NEETSS_Plan_{plan_id}.{ext}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export plan: {e}")
