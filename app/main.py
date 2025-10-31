from __future__ import annotations
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import date
from .planner import build_schedule

app = FastAPI(title="NEET SS Study Planner", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

class PlanRequest(BaseModel):
    start_date: date = Field(..., description="Start date (YYYY-MM-DD)")
    exam_date: date = Field(..., description="Exam date (YYYY-MM-DD, inclusive)")
    hours_per_day: float = Field(..., gt=0, description="Daily study hours (e.g., 5.5)")
    mocks: int = Field(2, ge=0, description="Number of mock exams to schedule")
    avg_minutes_per_mcq: float = Field(2.5, ge=2.0, le=3.0, description="Avg minutes per MCQ incl. review")

class PlanResponse(BaseModel):
    meta: dict
    schedule: list
    weekly_summaries: list

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/plan", response_model=PlanResponse)
def generate_plan(req: PlanRequest):
    if req.exam_date <= req.start_date:
        raise ValueError("exam_date must be after start_date.")
    plan = build_schedule(
        start_date=req.start_date,
        exam_date=req.exam_date,
        hours_per_day=req.hours_per_day,
        mocks=req.mocks,
        avg_mcq_minutes=req.avg_minutes_per_mcq
    )
    return plan
