"""
Planner v2 routes — consumed by the Flutter app and the new web SPA.

Everything in here is locked behind LMS-federated auth + the 3-day-grace
subscription gate (`enforce_subscription`). PDF download and `/me` are the
only routes that bypass the gate (handled inside `auth_lms.py`).

Routes (all prefixed `/api`):

  GET    /me                          — identity + renewal banner state
  GET    /me/today                    — today's day card (or placeholder)
  GET    /plans/current               — current active plan envelope
  POST   /plans/generate              — build a fresh plan from cfg
  GET    /plans/{plan_id}/week        — 14-day strip starting from `from`
  POST   /blocks/{block_id}/complete  — mark a block done
  POST   /blocks/{block_id}/skip
  POST   /blocks/{block_id}/snooze    — push to a later day
  POST   /blocks/reorder              — bulk drag-reorder

These are deliberately thin: the heavy lifting (bundle pull, schedule,
mastery merge) lives in services and content_scheduler.
"""
from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth_lms import LmsUser, enforce_subscription, get_lms_user
from app.database import get_db
from app.lms_client import LmsError, get_syllabus_bundle
from app.content_scheduler import SchedulerConfig, build_schedule
from app.ai.plan_rationale import generate_plan_rationale
from app.models import Plan, RecallCard, TopicMastery, User

logger = logging.getLogger("planner.v2")
router = APIRouter(prefix="/api", tags=["Planner v2"])


# ───────────────────────── helpers ─────────────────────────


def _get_or_create_local_user(db: Session, lms_user: LmsUser) -> User:
    """
    Provision a local row keyed by lms_user_id the first time an LMS user
    hits the planner. Legacy email-keyed users are linked on first sight.
    """
    user = db.query(User).filter(User.lms_user_id == lms_user.lms_user_id).one_or_none()
    if user:
        return user

    raw = lms_user.raw_state
    email = raw.get("email")

    # Try to link a legacy planner-only user by email.
    if email:
        legacy = db.query(User).filter(User.email == email, User.lms_user_id.is_(None)).one_or_none()
        if legacy:
            legacy.lms_user_id = lms_user.lms_user_id
            legacy.subscription_status = lms_user.subscription_status
            db.commit()
            return legacy

    user = User(
        name=raw.get("name") or "Planner User",
        email=email or f"{lms_user.lms_user_id}@lms.local",
        password_hash=None,
        lms_user_id=lms_user.lms_user_id,
        subscription_status=lms_user.subscription_status,
        last_lms_sync_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _load_mastery(db: Session, user_id: int) -> Dict[str, Dict[str, Any]]:
    """Load TopicMastery rows into the dict shape the scheduler expects."""
    rows = db.query(TopicMastery).filter(TopicMastery.user_id == user_id).all()
    today = datetime.utcnow()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        days_idle = 999
        if r.last_studied_at:
            days_idle = max(0, (today - r.last_studied_at).days)
        out[r.lms_topic_id] = {
            "mastery": r.mastery_score or 0.0,
            "accuracy": (r.correct / r.attempts) if r.attempts > 0 else 0.0,
            "last_studied_days_ago": days_idle,
        }
    return out


def _load_due_recall(db: Session, user_id: int) -> List[Dict[str, Any]]:
    today = date.today()
    rows = (
        db.query(RecallCard)
        .filter(RecallCard.user_id == user_id, RecallCard.next_review_date <= today)
        .order_by(RecallCard.next_review_date.asc())
        .limit(60)
        .all()
    )
    return [{"id": r.id, "topic": r.topic, "subtopic": r.subtopic} for r in rows]


def _user_multipliers(user: User) -> Dict[str, float]:
    return {
        "read": user.time_multiplier_read or 1.0,
        "watch": user.time_multiplier_watch or 1.0,
        "mcq": user.time_multiplier_mcq or 1.0,
    }


def _find_day(plan_data: Dict[str, Any], target_iso: str) -> Optional[Dict[str, Any]]:
    for d in plan_data.get("days", []):
        if d.get("day") == target_iso:
            return d
    return None


def _find_block(plan_data: Dict[str, Any], block_id: str) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    for d in plan_data.get("days", []):
        for b in d.get("blocks", []):
            if b.get("block_id") == block_id:
                return d, b
    return None, None


# ───────────────────────── models ─────────────────────────


class GeneratePlanRequest(BaseModel):
    name: str = "My Plan"
    exam_date: date
    hours_per_day: float = Field(4.0, ge=1.0, le=12.0)
    rest_days_per_week: int = Field(1, ge=0, le=2)
    custom_rest_dates: List[date] = Field(default_factory=list)
    use_lms_content: bool = True


class SnoozeRequest(BaseModel):
    until: date


class ReorderMove(BaseModel):
    block_id: str
    target_day: date
    target_position: int


class ReorderRequest(BaseModel):
    moves: List[ReorderMove]


# ───────────────────────── routes ─────────────────────────


@router.get("/me")
def me(request: Request, lms_user: LmsUser = Depends(get_lms_user), db: Session = Depends(get_db)):
    """
    Returns the planner's view of the user. NEVER 402s — always reachable
    so the client knows whether to render the SubscriptionGate.
    """
    user = _get_or_create_local_user(db, lms_user)
    return {
        "lms_user_id": user.lms_user_id,
        "name": user.name,
        "email": user.email,
        "exam_type": user.exam_type,
        "subscription_status": lms_user.subscription_status,
        "days_to_expiry": lms_user.days_to_expiry,
        "days_since_expiry": lms_user.days_since_expiry,
        "renewal_banner": (
            lms_user.subscription_status in ("grace",)
            or (lms_user.days_to_expiry is not None and lms_user.days_to_expiry <= 7)
        ),
        "leaderboard_opt_in": user.leaderboard_opt_in,
    }


@router.get("/me/today")
def me_today(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """
    Returns today's day card from the user's most recent active plan.
    Falls back to a "no plan yet" placeholder so the Flutter Today screen
    has something to render.
    """
    user = _get_or_create_local_user(db, lms_user)
    plan = (
        db.query(Plan)
        .filter(Plan.user_id == user.id)
        .order_by(Plan.created_at.desc())
        .first()
    )
    if not plan:
        return {
            "has_plan": False,
            "message": "No plan yet. Create one in 90 seconds.",
            "day": None,
        }

    today_iso = date.today().isoformat()
    day = _find_day(plan.data_json or {}, today_iso)
    if not day:
        # Plan exists but today is past its end (or before start) — surface a hint.
        return {"has_plan": True, "plan_id": plan.id, "day": None, "message": "No day card for today."}

    return {
        "has_plan": True,
        "plan_id": plan.id,
        "plan_name": plan.name,
        "needs_rebuild": plan.needs_rebuild,
        "day": day,
        "ai_rationale_md": plan.ai_rationale_md,
    }


@router.get("/plans/current")
def plans_current(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    plan = db.query(Plan).filter(Plan.user_id == user.id).order_by(Plan.created_at.desc()).first()
    if not plan:
        return {"plan": None}
    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "use_lms_content": plan.use_lms_content,
            "needs_rebuild": plan.needs_rebuild,
            "created_at": plan.created_at.isoformat(),
            "data": plan.data_json,
            "ai_rationale_md": plan.ai_rationale_md,
        }
    }


@router.post("/plans/generate")
def plans_generate(
    payload: GeneratePlanRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)

    bundle: Dict[str, Any] = {}
    if payload.use_lms_content:
        try:
            bundle = get_syllabus_bundle(lms_user.token)
        except LmsError as e:
            logger.warning("[plans/generate] bundle fetch failed: %s", e)
            bundle = {}

    cfg = SchedulerConfig(
        start_date=date.today(),
        exam_date=payload.exam_date,
        hours_per_day=payload.hours_per_day,
        rest_days_per_week=payload.rest_days_per_week,
        custom_rest_dates=payload.custom_rest_dates,
        use_lms_content=payload.use_lms_content and bool(bundle.get("categories")),
    )

    mastery = _load_mastery(db, user.id)
    due = _load_due_recall(db, user.id)
    days = build_schedule(bundle, cfg, mastery=mastery, due_recall_cards=due, user_multipliers=_user_multipliers(user))

    # AI rationale — Sonnet narrates the fresh plan. Failure is non-fatal:
    # the scheduler's per-block rationales are always present as a floor so
    # we never block plan creation on Claude availability.
    rationale: Optional[Dict[str, Any]] = None
    rationale_md: Optional[str] = None
    try:
        rationale = generate_plan_rationale(
            days=days,
            exam_date=payload.exam_date,
            hours_per_day=payload.hours_per_day,
            mastery=mastery,
            bundle=bundle,
            subscription_status=lms_user.subscription_status,
        )
        if rationale:
            rationale_md = rationale.get("rendered_md")
    except Exception as e:
        logger.warning("[plans/generate] rationale generation errored: %s", e)

    plan = Plan(
        user_id=user.id,
        name=payload.name,
        data_json={
            "days": days,
            "config": payload.model_dump(mode="json"),
            "bundle_version": bundle.get("version"),
            "rationale_meta": rationale,  # full structured payload for clients that want sections
        },
        config_json=payload.model_dump(mode="json"),
        use_lms_content=cfg.use_lms_content,
        content_bundle_etag=bundle.get("version"),
        ai_rationale_md=rationale_md,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    return {
        "plan_id": plan.id,
        "days_count": len(days),
        "first_day": days[0] if days else None,
        "use_lms_content": plan.use_lms_content,
        "ai_rationale_md": rationale_md,
        "ai_rationale_meta": rationale,
    }


@router.get("/plans/{plan_id}/week")
def plans_week(
    plan_id: int,
    from_: date = Query(..., alias="from"),
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    plan = db.query(Plan).filter(Plan.id == plan_id, Plan.user_id == user.id).first()
    if not plan:
        raise HTTPException(404, "plan not found")

    end = from_ + timedelta(days=13)
    days = [
        d for d in (plan.data_json or {}).get("days", [])
        if from_.isoformat() <= d.get("day", "") <= end.isoformat()
    ]
    return {"plan_id": plan.id, "from": from_.isoformat(), "to": end.isoformat(), "days": days}


def _mutate_plan(db: Session, user: User, mutator) -> Plan:
    plan = db.query(Plan).filter(Plan.user_id == user.id).order_by(Plan.created_at.desc()).first()
    if not plan:
        raise HTTPException(404, "no active plan")
    data = plan.data_json or {}
    mutator(data)
    plan.data_json = data
    # SQLAlchemy doesn't track in-place JSON mutations on all dialects — flag it.
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(plan, "data_json")
    db.commit()
    return plan


@router.post("/blocks/{block_id}/complete")
def block_complete(
    block_id: str,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)

    def _mut(data):
        _, b = _find_block(data, block_id)
        if not b:
            raise HTTPException(404, "block not found")
        b["state"] = "completed"
        b["completed_at"] = datetime.utcnow().isoformat()

    _mutate_plan(db, user, _mut)
    return {"ok": True, "block_id": block_id, "state": "completed"}


@router.post("/blocks/{block_id}/skip")
def block_skip(
    block_id: str,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)

    def _mut(data):
        _, b = _find_block(data, block_id)
        if not b:
            raise HTTPException(404, "block not found")
        b["state"] = "skipped"

    _mutate_plan(db, user, _mut)
    return {"ok": True, "block_id": block_id, "state": "skipped"}


@router.post("/blocks/{block_id}/snooze")
def block_snooze(
    block_id: str,
    payload: SnoozeRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)

    def _mut(data):
        src_day, b = _find_block(data, block_id)
        if not b or not src_day:
            raise HTTPException(404, "block not found")
        # Remove from source day
        src_day["blocks"] = [x for x in src_day["blocks"] if x.get("block_id") != block_id]
        # Insert into target day (create a Snoozed pseudo-day if it doesn't exist)
        target = _find_day(data, payload.until.isoformat())
        if not target:
            target = {
                "day": payload.until.isoformat(),
                "phase": "Snoozed",
                "time_budget_min": 0,
                "blocks": [],
                "checkpoint": {"expected_progress": None, "actual": None},
            }
            data.setdefault("days", []).append(target)
            data["days"].sort(key=lambda d: d.get("day", ""))
        b["state"] = "pending"
        target["blocks"].append(b)

    _mutate_plan(db, user, _mut)
    return {"ok": True, "block_id": block_id, "until": payload.until.isoformat()}


@router.post("/blocks/reorder")
def blocks_reorder(
    payload: ReorderRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)

    def _mut(data):
        for move in payload.moves:
            src_day, b = _find_block(data, move.block_id)
            if not b or not src_day:
                continue
            src_day["blocks"] = [x for x in src_day["blocks"] if x.get("block_id") != move.block_id]
            target = _find_day(data, move.target_day.isoformat())
            if not target:
                continue
            pos = max(0, min(move.target_position, len(target["blocks"])))
            target["blocks"].insert(pos, b)

    _mutate_plan(db, user, _mut)
    return {"ok": True, "moved": len(payload.moves)}
