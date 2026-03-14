from __future__ import annotations
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, JSON, Float,
    Boolean, Text, Date, UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    goal = Column(String(255), nullable=True)
    exam_type = Column(String(100), default="NEET_SS", nullable=False)
    leaderboard_opt_in = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    plans = relationship("Plan", back_populates="user", cascade="all, delete-orphan")
    progresses = relationship("Progress", back_populates="user", cascade="all, delete-orphan")
    mcq_scores = relationship("MCQScore", back_populates="user", cascade="all, delete-orphan")
    study_sessions = relationship("StudySession", back_populates="user", cascade="all, delete-orphan")
    daily_notes = relationship("DailyNote", back_populates="user", cascade="all, delete-orphan")
    recall_cards = relationship("RecallCard", back_populates="user", cascade="all, delete-orphan")


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    data_json = Column(JSON, nullable=False)
    config_json = Column(JSON, nullable=True)  # Stores plan generation params
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="plans")
    progress = relationship("Progress", back_populates="plan", uselist=False, cascade="all, delete-orphan")


class Progress(Base):
    __tablename__ = "progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False)
    progress_json = Column(JSON, nullable=False, default=dict)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )

    user = relationship("User", back_populates="progresses")
    plan = relationship("Plan", back_populates="progress")


class MCQScore(Base):
    """Per-session MCQ score logging with topic and subtopic breakdown."""
    __tablename__ = "mcq_scores"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=True)
    date = Column(Date, nullable=False)
    topic = Column(String(200), nullable=False)
    subtopic = Column(String(300), nullable=True)
    attempted = Column(Integer, nullable=False, default=0)
    correct = Column(Integer, nullable=False, default=0)
    time_minutes = Column(Float, nullable=True)
    source = Column(String(100), nullable=True)  # "MCQ ELITE", "Mock #3", etc.
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="mcq_scores")

    __table_args__ = (
        Index("ix_mcq_user_date", "user_id", "date"),
        Index("ix_mcq_user_topic", "user_id", "topic"),
    )


class StudySession(Base):
    """Pomodoro / study timer sessions."""
    __tablename__ = "study_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=True)
    date = Column(Date, nullable=False)
    topic = Column(String(200), nullable=True)
    session_type = Column(String(50), nullable=False, default="pomodoro")  # pomodoro, free, mock
    duration_minutes = Column(Float, nullable=False)
    completed = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="study_sessions")

    __table_args__ = (
        Index("ix_session_user_date", "user_id", "date"),
    )


class DailyNote(Base):
    """Free-text notes per day."""
    __tablename__ = "daily_notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=True)
    date = Column(Date, nullable=False)
    note = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="daily_notes")

    __table_args__ = (
        UniqueConstraint("user_id", "plan_id", "date", name="uq_note_user_plan_date"),
    )


class RecallCard(Base):
    """SM-2 spaced repetition tracking per topic."""
    __tablename__ = "recall_cards"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic = Column(String(200), nullable=False)
    subtopic = Column(String(300), nullable=True)

    # SM-2 state
    ease_factor = Column(Float, default=2.5, nullable=False)
    interval_days = Column(Float, default=1, nullable=False)
    repetitions = Column(Integer, default=0, nullable=False)
    next_review_date = Column(Date, nullable=False)
    last_quality = Column(Integer, default=0, nullable=False)  # 0-5

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="recall_cards")

    __table_args__ = (
        UniqueConstraint("user_id", "topic", "subtopic", name="uq_recall_user_topic_sub"),
        Index("ix_recall_next_review", "user_id", "next_review_date"),
    )
