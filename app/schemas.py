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
    exam_type: str = "NEET_SS"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    goal: Optional[str] = None
    exam_type: str = "NEET_SS"
    leaderboard_opt_in: bool = False

    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None
    exam_type: Optional[str] = None
    leaderboard_opt_in: Optional[bool] = None


# --------- Auth --------- #

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --------- Planner --------- #
# Legacy PlanRequest, PlanResponse, SavePlanRequest, PlanListItem retired —
# all plan generation now uses planner_v2's GeneratePlanRequest + inline
# response dicts. See app/routers/planner_v2.py.


# --------- Progress --------- #

class ProgressUpdate(BaseModel):
    progress: Dict[str, Any]


# --------- MCQ Scores --------- #

class MCQScoreCreate(BaseModel):
    plan_id: Optional[int] = None
    date: date
    topic: str
    subtopic: Optional[str] = None
    attempted: int = Field(..., ge=0)
    correct: int = Field(..., ge=0)
    time_minutes: Optional[float] = None
    source: Optional[str] = None
    notes: Optional[str] = None


class MCQScoreOut(BaseModel):
    id: int
    date: str
    topic: str
    subtopic: Optional[str] = None
    attempted: int
    correct: int
    accuracy: float
    time_minutes: Optional[float] = None
    source: Optional[str] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# --------- Study Sessions --------- #

class StudySessionCreate(BaseModel):
    plan_id: Optional[int] = None
    date: date
    topic: Optional[str] = None
    session_type: str = "pomodoro"
    duration_minutes: float = Field(..., gt=0)


class StudySessionOut(BaseModel):
    id: int
    date: str
    topic: Optional[str] = None
    session_type: str
    duration_minutes: float
    created_at: str

    class Config:
        from_attributes = True


# --------- Daily Notes --------- #

class DailyNoteUpdate(BaseModel):
    plan_id: Optional[int] = None
    date: date
    note: str


class DailyNoteOut(BaseModel):
    date: str
    note: str
    updated_at: str


# --------- Recall Cards (SM-2) --------- #

class RecallReview(BaseModel):
    topic: str
    subtopic: Optional[str] = None
    quality: int = Field(..., ge=0, le=5, description="0-2 fail, 3 hard, 4 good, 5 easy")


# --------- Analytics --------- #

class AnalyticsSummary(BaseModel):
    total_study_hours: float
    total_mcqs_attempted: int
    total_mcqs_correct: int
    overall_accuracy: float
    streak_days: int
    topics_started: int
    topics_total: int
    phase_progress: Dict[str, float]
    weekly_hours: List[Dict[str, Any]]
    topic_accuracy: List[Dict[str, Any]]
    heatmap: List[Dict[str, Any]]
    predicted_score: Optional[Dict[str, Any]] = None


# --------- Leaderboard --------- #

class LeaderboardEntry(BaseModel):
    rank: int
    name: str
    streak: int
    accuracy: float
    coverage: float
    study_hours: float


# --------- AI Coach --------- #

class AICoachRequest(BaseModel):
    plan_id: int


class AICoachResponse(BaseModel):
    analysis: str
    weak_topics: List[str]
    strong_topics: List[str]
    recommendations: List[str]
    adjusted_plan: Optional[Dict[str, Any]] = None
