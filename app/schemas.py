from __future__ import annotations
from typing import Optional, Any, Dict, List
from datetime import date
from pydantic import BaseModel, EmailStr, Field

# --------- Users --------- #
class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str
    goal: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    goal: Optional[str] = None
    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None

# --------- Auth --------- #
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

# --------- Planner --------- #
class PlanRequest(BaseModel):
    start_date: date = Field(..., description="Start date (YYYY-MM-DD)")
    exam_date: date = Field(..., description="Exam date (YYYY-MM-DD, inclusive)")
    hours_per_day: float = Field(..., gt=0, description="Daily study hours (e.g., 5.5)")
    mocks: int = Field(2, ge=0, description="Number of mock exams to schedule")
    avg_minutes_per_mcq: float = Field(2.5, ge=2.0, le=3.0)
    plan_type: str = Field("full", description="full|theory|mcq|revision|mock")

class PlanResponse(BaseModel):
    meta: Dict[str, Any]
    schedule: List[Dict[str, Any]]
    weekly_summaries: List[Dict[str, Any]]

# --------- Plans --------- #
class SavePlanRequest(BaseModel):
    name: str
    data: Dict[str, Any]  # The full plan JSON (PlanResponse)

class PlanListItem(BaseModel):
    id: int
    name: str
    created_at: str
    class Config:
        from_attributes = True

# --------- Progress --------- #
class ProgressUpdate(BaseModel):
    # arbitrary shape: e.g., {"days_done":[0,1,2], "weeks_done":[1], "checks":{"week-1":[true,false,true]}}
    progress: Dict[str, Any]

