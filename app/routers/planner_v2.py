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
from app.lms_client import (
    LmsError,
    get_syllabus_bundle,
    get_user_signal,
    get_user_daily_activity,
    get_user_mcq_history,
    get_user_mock_history,
    get_user_content_progress,
    send_otp_sms,
    login_via_sms,
)
from app.content_scheduler import (
    SchedulerConfig,
    build_schedule,
    build_schedule_from_signal,
)
from app.ai.plan_rationale import generate_plan_rationale
from app.ai.mastery import build_vector as build_mastery_vector, rank_weakness, coverage_pct, avg_mastery
from app.ai.recommender import suggest_next_actions
from app.models import Plan, RecallCard, TopicMastery, User

logger = logging.getLogger("planner.v2")
router = APIRouter(prefix="/api", tags=["Planner v2"])


# ───────────────────────── auth proxy (SMS OTP via Sushruta LMS) ─────────────────────────
#
# The planner does not maintain its own password database. All authentication
# goes through the Sushruta LMS mobile OTP flow (MSG91 SMS → WhatsappOtpModel
# — legacy table name, the actual delivery is MSG91 SMS) so the same account
# works in the Sushruta mobile app, the Sushruta web app, and Cortex.
#
# These two endpoints are thin server-side proxies that forward the browser
# request to the LMS (avoids CORS, hides the LMS origin, centralises error
# mapping). The LMS endpoints hit are exactly the ones the Sushruta app
# already uses in production:
#   POST /api/sendOtpViaSms   body: { phone }
#   POST /api/loginViaSms     body: { phone, userOTP, deviceType, ... }


class _OtpSendRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)


class _OtpVerifyRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)
    otp: str = Field(..., min_length=3, max_length=8)
    device_type: str = Field(default="desktop")
    device_id: str = Field(default="cortex-web")
    device_name: str = Field(default="Cortex Web")
    device_unique_id: str = Field(default="cortex-web")


@router.post("/auth/send-otp")
def auth_send_otp(body: _OtpSendRequest):
    """Step 1 — ask the LMS (via MSG91) to SMS a 4-digit OTP to this mobile."""
    try:
        send_otp_sms(body.phone)
    except LmsError as e:
        msg = str(e)
        if "must be a 10-digit" in msg:
            raise HTTPException(status_code=400, detail="Enter a valid 10-digit mobile number.")
        if "404" in msg or "not found" in msg.lower():
            raise HTTPException(status_code=404, detail="No Sushruta LGS App account found for that mobile. Create one first.")
        raise HTTPException(status_code=502, detail=f"Could not send OTP: {msg}")
    return {"ok": True, "message": "OTP sent. Check your SMS (expires in 2 minutes)."}


@router.post("/auth/verify-otp")
def auth_verify_otp(body: _OtpVerifyRequest):
    """Step 2 — verify SMS OTP and return an LMS session token the planner accepts."""
    try:
        resp = login_via_sms(
            phone=body.phone,
            otp=body.otp.strip(),
            device_type=body.device_type,
            device_id=body.device_id,
            device_name=body.device_name,
            device_unique_id=body.device_unique_id,
        )
    except LmsError as e:
        raise HTTPException(status_code=401, detail=f"OTP verification failed: {e}")

    # LMS wraps success responses in { status, data: { token, isActive, ... } }
    data = resp.get("data") if isinstance(resp, dict) else None
    token = None
    if isinstance(data, dict):
        token = data.get("token") or data.get("access_token")
    if not token and isinstance(resp, dict):
        token = resp.get("token") or resp.get("access_token")
    if not token:
        raise HTTPException(status_code=502, detail="LMS did not return a session token.")
    return {"ok": True, "token": token, "lms_response": data or {}}


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


def _persist_mastery_vector(
    db: Session,
    user_id: int,
    mastery_vector: Dict[str, Dict[str, Any]],
) -> None:
    """
    Upsert TopicMastery rows from the freshly computed mastery vector. We
    store the *primitives* (attempts/correct/coverage/last_studied/recall_strength)
    plus the cached composite `mastery_score` so the Progress radar and the
    Analytics weakest-list can render without re-querying the LMS.
    """
    if not mastery_vector:
        return
    now = datetime.utcnow()
    existing = {
        r.lms_topic_id: r
        for r in db.query(TopicMastery)
        .filter(TopicMastery.user_id == user_id)
        .all()
    }
    for tid, row in (mastery_vector or {}).items():
        if not tid:
            continue
        drivers = (row or {}).get("drivers", {}) or {}
        attempted = int(drivers.get("attempted", 0) or 0)
        correct = int(round(attempted * (drivers.get("accuracy", 0.0) or 0.0) / 100.0))
        last_studied = None
        days_ago = drivers.get("days_since_last")
        if isinstance(days_ago, (int, float)) and days_ago < 9000:
            last_studied = now - timedelta(days=float(days_ago))

        rec = existing.get(tid)
        if rec is None:
            rec = TopicMastery(
                user_id=user_id,
                lms_topic_id=tid,
                topic_name=(row or {}).get("topic_name"),
                attempts=attempted,
                correct=correct,
                coverage_pct=float(drivers.get("coverage", 0.0) or 0.0),
                last_studied_at=last_studied,
                recall_strength=drivers.get("recall_strength"),
                mastery_score=float((row or {}).get("mastery", 0.0) or 0.0),
                theta=drivers.get("theta"),
            )
            db.add(rec)
        else:
            rec.topic_name = (row or {}).get("topic_name") or rec.topic_name
            rec.attempts = attempted
            rec.correct = correct
            rec.coverage_pct = float(drivers.get("coverage", rec.coverage_pct) or rec.coverage_pct)
            if last_studied is not None:
                rec.last_studied_at = last_studied
            if drivers.get("recall_strength") is not None:
                rec.recall_strength = drivers.get("recall_strength")
            rec.mastery_score = float((row or {}).get("mastery", rec.mastery_score) or rec.mastery_score)
    db.commit()


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
    """
    New plan generation schema. Uses start_date + end_date as the canonical
    window (exam_date is accepted as a back-compat alias and folded into
    end_date during validation).
    """
    name: str = "My Plan"
    # Either pair works — new flow uses start_date+end_date, legacy uses exam_date.
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    exam_date: Optional[date] = None  # legacy alias for end_date
    # Sizing knobs
    hours_per_day: float = Field(4.0, ge=1.0, le=12.0)
    daily_minutes: Optional[int] = Field(None, ge=30, le=720)
    rest_days_per_week: int = Field(1, ge=0, le=2)
    custom_rest_dates: List[date] = Field(default_factory=list)
    # Generator-form additions matching the old planner
    mocks_count: Optional[int] = Field(None, ge=0, le=50)
    min_per_mcq: float = Field(1.5, ge=0.5, le=5.0)
    revision_rounds: int = Field(1, ge=1, le=3)
    focus_topic_ids: List[str] = Field(default_factory=list)
    use_lms_content: bool = True

    def resolved_start(self) -> date:
        return self.start_date or date.today()

    def resolved_end(self) -> date:
        return self.end_date or self.exam_date or (date.today() + timedelta(days=90))


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
    user_signal: Dict[str, Any] = {}
    if payload.use_lms_content:
        try:
            bundle = get_syllabus_bundle(lms_user.token)
        except LmsError as e:
            logger.warning("[plans/generate] bundle fetch failed: %s", e)
            bundle = {}
        try:
            user_signal = get_user_signal(lms_user.token)
        except LmsError as e:
            logger.warning("[plans/generate] user-signal fetch failed: %s", e)
            user_signal = {}

    start = payload.resolved_start()
    end = payload.resolved_end()
    if end <= start:
        raise HTTPException(400, "end_date must be after start_date")

    cfg = SchedulerConfig(
        start_date=start,
        end_date=end,
        hours_per_day=payload.hours_per_day,
        rest_days_per_week=payload.rest_days_per_week,
        custom_rest_dates=payload.custom_rest_dates,
        use_lms_content=payload.use_lms_content and bool(bundle.get("categories")),
        daily_minutes=payload.daily_minutes,
        mocks_count=payload.mocks_count,
        min_per_mcq=payload.min_per_mcq,
        revision_rounds=payload.revision_rounds,
        focus_topic_ids=payload.focus_topic_ids,
    )

    due = _load_due_recall(db, user.id)
    # Use the new orchestrator that pulls in LMS signal + builds the mastery
    # vector from real performance data, not just local DB heuristics.
    days, mastery_vector = build_schedule_from_signal(
        bundle=bundle,
        cfg=cfg,
        user_signal=user_signal,
        fsrs_cards_by_topic=None,  # TODO: hydrate from RecallCard rows
        due_recall_cards=due,
        user_multipliers=_user_multipliers(user),
    )

    # Persist the mastery vector into TopicMastery so the Progress radar +
    # Analytics weakest-list can read it without re-querying the LMS.
    try:
        _persist_mastery_vector(db, user.id, mastery_vector)
    except Exception as e:
        logger.warning("[plans/generate] mastery persist failed: %s", e)

    # Legacy local mastery fallback (kept for plan_rationale which still
    # accepts the old shape).
    mastery = _load_mastery(db, user.id)

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

    # Archive any prior active plans for this user — only one is "current".
    db.query(Plan).filter(
        Plan.user_id == user.id, Plan.is_archived == False  # noqa: E712
    ).update({"is_archived": True}, synchronize_session=False)

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
        # Phase 2: explicit window so the nightly replan job can find this plan.
        start_date=start,
        end_date=end,
        daily_minutes=cfg.effective_daily_minutes,
        is_archived=False,
        last_replan_at=None,
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


# ═══════════════════════════════════════════════════════════════════
#  ML / signal endpoints — feed the Dashboard, AI Coach & Analytics
# ═══════════════════════════════════════════════════════════════════


def _fresh_signal_and_vector(
    db: Session, user: User, lms_user: LmsUser
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Pull bundle + LMS signal and rebuild the mastery vector. Tolerant of LMS hiccups."""
    bundle: Dict[str, Any] = {}
    user_signal: Dict[str, Any] = {}
    try:
        bundle = get_syllabus_bundle(lms_user.token) or {}
    except LmsError as e:
        logger.warning("[ml] bundle fetch failed: %s", e)
    try:
        user_signal = get_user_signal(lms_user.token) or {}
    except LmsError as e:
        logger.warning("[ml] user-signal fetch failed: %s", e)

    # Flatten bundle topics for the mastery builder.
    bundle_topics: List[Dict[str, Any]] = []
    for cat in (bundle.get("categories") or []):
        for sub in (cat.get("subcategories") or []):
            for t in (sub.get("topics") or []):
                bundle_topics.append({
                    "topic_id": str(t.get("_id") or t.get("topic_id") or ""),
                    "topic_name": t.get("name", ""),
                    "category": cat.get("name", ""),
                    "subcategory": sub.get("name", ""),
                })

    vector = build_mastery_vector(
        lms_signal=user_signal,
        fsrs_cards_by_topic=None,
        bundle_topics=bundle_topics,
    )
    return bundle, user_signal, vector


@router.get("/signal")
def signal_passthrough(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """Raw composite signal — used by Dashboard hero tiles & Analytics."""
    user = _get_or_create_local_user(db, lms_user)
    try:
        sig = get_user_signal(lms_user.token) or {}
    except LmsError as e:
        logger.warning("[signal] fetch failed: %s", e)
        sig = {}
    return {"user_id": user.id, "signal": sig}


@router.get("/streak")
def streak(
    days: int = Query(60, ge=1, le=365),
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """Daily activity + streak — drives the Dashboard streak ring."""
    _ = _get_or_create_local_user(db, lms_user)
    try:
        data = get_user_daily_activity(lms_user.token, days=days) or {}
    except LmsError as e:
        logger.warning("[streak] fetch failed: %s", e)
        data = {}
    streak_block = (data or {}).get("streak", {}) or {}
    return {
        "current": streak_block.get("current", 0),
        "longest": streak_block.get("longest", 0),
        "avg_minutes_last_14d": (data or {}).get("avg_minutes_last_14d", 0),
        "days": (data or {}).get("days", []),
    }


@router.get("/ml/mastery")
def ml_mastery(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """
    Returns the live mastery vector + summary (coverage, avg, weakest list).
    Persists into TopicMastery as a side-effect so the radar reads instantly
    on the next dashboard load.
    """
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _fresh_signal_and_vector(db, user, lms_user)
    try:
        _persist_mastery_vector(db, user.id, vector)
    except Exception as e:
        logger.warning("[ml/mastery] persist failed: %s", e)
    weakest = rank_weakness(vector, top_n=8)
    return {
        "vector": vector,
        "coverage_pct": coverage_pct(vector),
        "avg_mastery": avg_mastery(vector),
        "weakest": weakest,
    }


@router.get("/ml/recommendations")
def ml_recommendations(
    n: int = Query(3, ge=1, le=10),
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """Claude-Haiku-backed 'what to do next' recommendations."""
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _fresh_signal_and_vector(db, user, lms_user)
    actions = suggest_next_actions(
        user_signal=signal,
        mastery_vector=vector,
        bundle=bundle,
        n=n,
    )
    return {"recommendations": actions, "count": len(actions)}


@router.post("/ml/replan")
def ml_replan(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """
    Manual trigger for the same logic the nightly job will run: pull fresh
    LMS signal, rebuild the mastery vector, and regenerate the schedule on
    the user's *current* plan window without changing its name or dates.
    """
    user = _get_or_create_local_user(db, lms_user)
    plan = (
        db.query(Plan)
        .filter(Plan.user_id == user.id, Plan.is_archived == False)  # noqa: E712
        .order_by(Plan.created_at.desc())
        .first()
    )
    if not plan:
        raise HTTPException(404, "no active plan to replan")
    if not plan.start_date or not plan.end_date:
        raise HTTPException(400, "plan missing start_date/end_date — regenerate it once with the new flow")

    try:
        bundle = get_syllabus_bundle(lms_user.token) or {}
    except LmsError:
        bundle = {}
    try:
        user_signal = get_user_signal(lms_user.token) or {}
    except LmsError:
        user_signal = {}

    cfg_in = plan.config_json or {}
    cfg = SchedulerConfig(
        start_date=date.today() if date.today() > plan.start_date else plan.start_date,
        end_date=plan.end_date,
        hours_per_day=float(cfg_in.get("hours_per_day", 4.0)),
        rest_days_per_week=int(cfg_in.get("rest_days_per_week", 1)),
        custom_rest_dates=[date.fromisoformat(s) for s in cfg_in.get("custom_rest_dates", []) if isinstance(s, str)],
        use_lms_content=bool(plan.use_lms_content and bundle.get("categories")),
        daily_minutes=plan.daily_minutes or cfg_in.get("daily_minutes"),
        mocks_count=cfg_in.get("mocks_count"),
        min_per_mcq=float(cfg_in.get("min_per_mcq", 1.5)),
        revision_rounds=int(cfg_in.get("revision_rounds", 1)),
        focus_topic_ids=cfg_in.get("focus_topic_ids", []) or [],
    )

    due = _load_due_recall(db, user.id)
    days, mastery_vector = build_schedule_from_signal(
        bundle=bundle,
        cfg=cfg,
        user_signal=user_signal,
        fsrs_cards_by_topic=None,
        due_recall_cards=due,
        user_multipliers=_user_multipliers(user),
    )
    try:
        _persist_mastery_vector(db, user.id, mastery_vector)
    except Exception as e:
        logger.warning("[ml/replan] persist failed: %s", e)

    data = plan.data_json or {}
    data["days"] = days
    data["bundle_version"] = bundle.get("version")
    plan.data_json = data
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(plan, "data_json")
    plan.needs_rebuild = False
    plan.last_replan_at = datetime.utcnow()
    plan.last_rebuild_at = datetime.utcnow()
    db.commit()

    return {
        "ok": True,
        "plan_id": plan.id,
        "days_count": len(days),
        "replanned_at": plan.last_replan_at.isoformat(),
        "weakest_topic_count": len(rank_weakness(mastery_vector, top_n=5)),
    }
