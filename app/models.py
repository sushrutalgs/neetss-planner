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
    # password_hash is now nullable — federated LMS users don't have a local password.
    password_hash = Column(String(255), nullable=True)
    goal = Column(String(255), nullable=True)
    exam_type = Column(String(100), default="NEET_SS", nullable=False)
    leaderboard_opt_in = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ───── LMS federation (Phase 0/1) ─────
    # Mongo ObjectId of the LMS user this row is bound to. Nullable so legacy
    # planner-only signups keep working until migration.
    lms_user_id = Column(String(64), unique=True, index=True, nullable=True)
    subscription_status = Column(String(20), nullable=True)        # active|grace|locked|none
    subscription_expires_at = Column(DateTime, nullable=True)
    last_lms_sync_at = Column(DateTime, nullable=True)

    # Per-user calibration learned from study_sessions vs estimated_minutes.
    # Used by the content scheduler so day cards stop lying about time.
    time_multiplier_read = Column(Float, default=1.0, nullable=False)
    time_multiplier_watch = Column(Float, default=1.0, nullable=False)
    time_multiplier_mcq = Column(Float, default=1.0, nullable=False)

    plans = relationship("Plan", back_populates="user", cascade="all, delete-orphan")
    progresses = relationship("Progress", back_populates="user", cascade="all, delete-orphan")
    mcq_scores = relationship("MCQScore", back_populates="user", cascade="all, delete-orphan")
    study_sessions = relationship("StudySession", back_populates="user", cascade="all, delete-orphan")
    daily_notes = relationship("DailyNote", back_populates="user", cascade="all, delete-orphan")
    recall_cards = relationship("RecallCard", back_populates="user", cascade="all, delete-orphan")
    topic_mastery = relationship("TopicMastery", back_populates="user", cascade="all, delete-orphan")


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    data_json = Column(JSON, nullable=False)
    config_json = Column(JSON, nullable=True)  # Stores plan generation params
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ───── Phase 1 content-aware additions ─────
    use_lms_content = Column(Boolean, default=True, nullable=False)
    needs_rebuild = Column(Boolean, default=False, nullable=False, index=True)
    last_rebuild_at = Column(DateTime, nullable=True)
    content_bundle_etag = Column(String(64), nullable=True)
    ai_rationale_md = Column(Text, nullable=True)  # Sonnet plan-rationale narrative

    # ───── Phase 2: explicit date window (replaces exam_date semantics) ─────
    # start_date and end_date are the canonical plan window. The old exam_date
    # is still readable from config_json for legacy plans but new plans use
    # these columns so the nightly replan job can query active plans cheaply.
    start_date = Column(Date, nullable=True, index=True)
    end_date = Column(Date, nullable=True, index=True)
    daily_minutes = Column(Integer, nullable=True)  # target minutes/day from generator
    is_archived = Column(Boolean, default=False, nullable=False, index=True)
    last_replan_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="plans")
    progress = relationship("Progress", back_populates="plan", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_plan_user_active", "user_id", "is_archived", "end_date"),
    )


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

    # ───── Phase 1: link back to specific LMS content + IRT difficulty ─────
    lms_topic_id = Column(String(64), nullable=True, index=True)
    content_id = Column(String(64), nullable=True)        # specific MCQ doc id
    difficulty = Column(Float, nullable=True)             # IRT b-param at attempt time
    time_seconds = Column(Integer, nullable=True)

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
    session_type = Column(String(50), nullable=False, default="pomodoro")  # pomodoro, free, mock, read, watch
    duration_minutes = Column(Float, nullable=False)
    completed = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ───── Phase 1: feedback loop for time-multiplier learning ─────
    estimated_minutes = Column(Float, nullable=True)   # what the planner predicted
    actual_minutes = Column(Float, nullable=True)      # what we measured
    content_id = Column(String(64), nullable=True)
    lms_topic_id = Column(String(64), nullable=True)
    block_id = Column(String(64), nullable=True)

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
    """
    Spaced repetition card. Originally SM-2; Phase 1 adds FSRS-5 fields
    side-by-side so we can migrate users gradually. The recall.py router
    decides which scheduler to use per card based on `algo`.
    """
    __tablename__ = "recall_cards"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    topic = Column(String(200), nullable=False)
    subtopic = Column(String(300), nullable=True)

    # ───── Legacy SM-2 state (kept for backwards compat) ─────
    ease_factor = Column(Float, default=2.5, nullable=False)
    interval_days = Column(Float, default=1, nullable=False)
    repetitions = Column(Integer, default=0, nullable=False)
    next_review_date = Column(Date, nullable=False)
    last_quality = Column(Integer, default=0, nullable=False)  # 0-5

    # ───── FSRS-5 state (Phase 1) ─────
    algo = Column(String(10), default="sm2", nullable=False)  # 'sm2' | 'fsrs'
    fsrs_stability = Column(Float, nullable=True)
    fsrs_difficulty = Column(Float, nullable=True)
    fsrs_due = Column(DateTime, nullable=True)
    fsrs_state = Column(String(20), nullable=True)  # 'new'|'learning'|'review'|'relearning'

    # ───── LMS deep-link (Phase 1) ─────
    lms_topic_id = Column(String(64), nullable=True, index=True)
    content_id = Column(String(64), nullable=True)  # the note/video this card was generated from

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="recall_cards")

    __table_args__ = (
        UniqueConstraint("user_id", "topic", "subtopic", name="uq_recall_user_topic_sub"),
        Index("ix_recall_next_review", "user_id", "next_review_date"),
        Index("ix_recall_fsrs_due", "user_id", "fsrs_due"),
    )


# ════════════════════════════════════════════════════════════
#  Phase 1 — new tables
# ════════════════════════════════════════════════════════════


class TopicMastery(Base):
    """
    Per-(user, lms_topic_id) mastery substrate. Updated on every MCQ score
    write or recall review. The composite mastery score in [0,1] is computed
    in app/ml/mastery.py from these primitives — we store the primitives, not
    the score, so the formula can change without a backfill.
    """
    __tablename__ = "topic_mastery"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    lms_topic_id = Column(String(64), nullable=False)
    topic_name = Column(String(255), nullable=True)  # denormalized for fast UI

    # Bayesian-smoothed accuracy primitives
    attempts = Column(Integer, default=0, nullable=False)
    correct = Column(Integer, default=0, nullable=False)

    # IRT theta (1PL/Rasch fit), updated nightly
    theta = Column(Float, nullable=True)

    coverage_pct = Column(Float, default=0.0, nullable=False)   # frac of content_ids checked off
    last_studied_at = Column(DateTime, nullable=True)
    recall_strength = Column(Float, nullable=True)              # avg ease/stability of recall cards
    mastery_score = Column(Float, nullable=True)                # cached composite, written by ml.mastery

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="topic_mastery")

    __table_args__ = (
        UniqueConstraint("user_id", "lms_topic_id", name="uq_mastery_user_topic"),
        Index("ix_mastery_user_score", "user_id", "mastery_score"),
    )


class AIRun(Base):
    """
    Audit trail for every Claude call. Cost dashboard, reproducibility,
    and the data the prompt-debug screen reads from.
    """
    __tablename__ = "ai_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    surface = Column(String(50), nullable=False, index=True)
    # 'daily_briefing' | 'weekly_coach' | 'plan_rationale' | 'mock_analysis' |
    # 'weak_topic_dive' | 'qa_chat' | 'syllabus_mapping' | 'recall_gen' | 'push_copy'
    model = Column(String(50), nullable=False)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    cache_read_tokens = Column(Integer, default=0, nullable=False)
    cache_write_tokens = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Integer, nullable=True)
    stop_reason = Column(String(30), nullable=True)
    cost_usd = Column(Float, nullable=True)
    prompt_hash = Column(String(64), nullable=True, index=True)  # for caching/dedup
    output_md = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_airuns_user_surface", "user_id", "surface"),
    )


class WebhookInbox(Base):
    """
    Durable record of every inbound LMS webhook so we can replay after a
    crash and dedupe across restarts (the in-memory ring in routers/webhook.py
    is the fast path, this is the safety net).
    """
    __tablename__ = "webhook_inbox"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    payload_json = Column(JSON, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)


class SyllabusMapping(Base):
    """
    The bridge between the planner's hardcoded SYLLABUS_TREE (Sabiston/
    Schwartz/Bailey chapters) and the LMS Mongo hierarchy. Populated by
    scripts/build_syllabus_mapping.py (Sonnet 4.6) and editable by admins
    via the LMS restructurer UI.
    """
    __tablename__ = "syllabus_mapping"

    id = Column(Integer, primary_key=True, index=True)
    planner_topic = Column(String(200), nullable=False)
    planner_subtopic = Column(String(300), nullable=False)
    lms_topic_ids = Column(JSON, nullable=False, default=list)   # ["...", "..."]
    lms_content_ids = Column(JSON, nullable=True, default=list)
    confidence = Column(Float, nullable=True)
    source = Column(String(20), default="claude", nullable=False)  # 'claude' | 'manual'
    verified_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("planner_topic", "planner_subtopic", name="uq_syllabus_pair"),
    )


class NoteChunk(Base):
    """
    Chunked + embedded LMS Notes for the RAG store. Populated by the nightly
    ingest job. Embeddings live in pgvector — the column is declared as JSON
    here so the model loads on a vanilla SQLAlchemy without the pgvector
    extension; the migration script swaps the type to vector(1024) on real
    Postgres.
    """
    __tablename__ = "note_chunks"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(String(64), nullable=False, index=True)
    lms_topic_id = Column(String(64), nullable=True, index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=True)  # 1024-d float vector
    page_anchor = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("content_id", "chunk_index", name="uq_chunk_content_idx"),
    )
