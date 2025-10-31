from __future__ import annotations
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from datetime import date
import os

from .planner import build_schedule

# -------------------------
# FastAPI app setup
# -------------------------
app = FastAPI(title="NEET SS Study Planner", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Mount static directory
# -------------------------
static_dir = os.path.join(os.getcwd(), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print("⚠️ Warning: static folder not found. Make sure /static/index.html exists.")

# -------------------------
# Pydantic models
# -------------------------
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

# -------------------------
# API endpoints
# -------------------------
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

# -------------------------
# Serve index.html at root
# -------------------------
@app.get("/")
def serve_root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": "index.html not found. Please ensure /static/index.html exists."}
