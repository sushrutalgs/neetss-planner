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


def _count_plan_inclusions(days: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Walk the scheduler's day/block/item tree and tally unique content
    referenced in the plan so the dashboard can show
    "X videos · Y notes · Z MCQs · M mocks".

    A single piece of content may appear on multiple days (revision passes) —
    we dedupe by content_id per kind so totals reflect the *library* the plan
    will expose, not the number of impressions.
    """
    videos: set = set()
    notes: set = set()
    mcq_sets: set = set()
    mocks: set = set()
    total_minutes = 0
    minutes_by_kind: Dict[str, int] = {"read": 0, "watch": 0, "practice": 0, "mock": 0, "recall": 0}
    mcq_question_target = 0  # approx total questions planned (items * 10 fallback)

    for day in days or []:
        for block in day.get("blocks", []) or []:
            kind = block.get("kind") or ""
            mins = int(block.get("minutes") or 0)
            total_minutes += mins
            if kind in minutes_by_kind:
                minutes_by_kind[kind] += mins
            for item in block.get("items", []) or []:
                ck = item.get("content_kind")
                cid = str(item.get("content_id") or "")
                if not cid:
                    continue
                if ck == "video":
                    videos.add(cid)
                elif ck == "notes":
                    notes.add(cid)
                elif ck == "mcq_set":
                    mcq_sets.add(cid)
                    mcq_question_target += int(item.get("question_count") or item.get("count") or 10)
                elif ck == "mock":
                    mocks.add(cid)

    return {
        "videos": len(videos),
        "notes": len(notes),
        "mcq_sets": len(mcq_sets),
        "mcqs_target": mcq_question_target,
        "mocks": len(mocks),
        "total_minutes": total_minutes,
        "total_hours": round(total_minutes / 60, 1),
        "minutes_by_kind": minutes_by_kind,
        "_ids": {
            "videos": list(videos),
            "notes": list(notes),
            "mcq_sets": list(mcq_sets),
            "mocks": list(mocks),
        },
    }


@router.get("/library/summary")
def library_summary(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """
    "What's in my Sushruta app" snapshot — the library the planner will
    draw from when you generate a plan. Walks the syllabus bundle once
    and counts every unique piece of content the LMS exposes to this user.

    Returns:
      {
        has_bundle, bundle_version,
        totals: {
          videos, notes, mcqs, mocks, topics, subcategories, categories,
          total_content
        },
        by_category: [
          { category, topics, videos, notes, mcqs, mocks }
        ],
        top_topics: [
          { topic_id, name, category, videos, notes, mcqs }
        ]
      }
    """
    try:
        bundle = get_syllabus_bundle(lms_user.token) or {}
    except LmsError as e:
        logger.warning("[library/summary] bundle fetch failed: %s", e)
        return {"has_bundle": False, "error": str(e)}

    categories = bundle.get("categories") or []
    if not categories:
        return {"has_bundle": False}

    total_videos: set = set()
    total_notes: set = set()
    total_mcqs = 0
    total_mocks: set = set()
    total_topics = 0
    total_subcats = 0

    by_category: List[Dict[str, Any]] = []
    topic_rows: List[Dict[str, Any]] = []

    for cat in categories:
        cat_name = cat.get("name") or cat.get("category_name") or "Uncategorised"
        cat_videos: set = set()
        cat_notes: set = set()
        cat_mcqs = 0
        cat_mocks: set = set()
        cat_topics = 0
        for sub in cat.get("subcategories") or []:
            total_subcats += 1
            for topic in sub.get("topics") or []:
                cat_topics += 1
                total_topics += 1
                cids = topic.get("content_ids") or {}
                counts = topic.get("content_counts") or {}
                vids = cids.get("videos") or []
                nts = cids.get("notes") or []
                mqs_cnt = int(counts.get("mcqs") or len(cids.get("mcqs") or []))
                for v in vids:
                    total_videos.add(str(v)); cat_videos.add(str(v))
                for n in nts:
                    total_notes.add(str(n)); cat_notes.add(str(n))
                cat_mcqs += mqs_cnt
                total_mcqs += mqs_cnt
                topic_rows.append({
                    "topic_id": str(topic.get("_id") or topic.get("id") or ""),
                    "name": topic.get("name") or topic.get("topic_name") or "",
                    "category": cat_name,
                    "videos": len(vids),
                    "notes": len(nts),
                    "mcqs": mqs_cnt,
                })
        by_category.append({
            "category": cat_name,
            "topics": cat_topics,
            "videos": len(cat_videos),
            "notes": len(cat_notes),
            "mcqs": cat_mcqs,
            "mocks": len(cat_mocks),
        })

    for mk in bundle.get("mocks_global") or []:
        total_mocks.add(str(mk.get("_id") or mk.get("id") or mk.get("title") or len(total_mocks)))

    top_topics = sorted(
        topic_rows,
        key=lambda t: (t["videos"] + t["notes"] + t["mcqs"]),
        reverse=True,
    )[:8]

    return {
        "has_bundle": True,
        "bundle_version": bundle.get("version"),
        "totals": {
            "videos": len(total_videos),
            "notes": len(total_notes),
            "mcqs": total_mcqs,
            "mocks": len(total_mocks),
            "topics": total_topics,
            "subcategories": total_subcats,
            "categories": len(categories),
            "total_content": len(total_videos) + len(total_notes) + total_mcqs + len(total_mocks),
        },
        "by_category": by_category,
        "top_topics": top_topics,
    }


@router.get("/plans/inclusions")
def plans_inclusions(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    """
    Dashboard "My Plan" card backing endpoint.

    Returns:
      {
        has_plan, plan_id, plan_name, start_date, end_date, days_total, days_elapsed,
        totals:   { videos, notes, mcq_sets, mcqs_target, mocks, total_hours, minutes_by_kind },
        consumed: { videos_watched, notes_opened, mcqs_attempted, mcqs_correct, mocks_done, minutes_spent_14d },
        progress: { videos_pct, notes_pct, mcqs_pct, mocks_pct, overall_pct },
        today:    { date, minutes_planned, blocks: [{kind,title,minutes,done}], completed_blocks, total_blocks }
      }

    Completely tolerant of missing LMS data — if the LMS side 500s we still
    return the plan-side totals so the card always renders something useful.
    """
    user = _get_or_create_local_user(db, lms_user)
    plan = (
        db.query(Plan)
        .filter(Plan.user_id == user.id, Plan.is_archived == False)  # noqa: E712
        .order_by(Plan.created_at.desc())
        .first()
    )
    if not plan:
        plan = db.query(Plan).filter(Plan.user_id == user.id).order_by(Plan.created_at.desc()).first()
    if not plan or not (plan.data_json or {}).get("days"):
        return {"has_plan": False}

    days = (plan.data_json or {}).get("days") or []
    totals = _count_plan_inclusions(days)

    # ── Consumed side: ask the LMS for the user's real progress ──
    consumed = {
        "videos_watched": 0,
        "notes_opened": 0,
        "mcqs_attempted": 0,
        "mcqs_correct": 0,
        "mocks_done": 0,
        "minutes_spent_14d": 0,
    }
    try:
        mcq_hist = get_user_mcq_history(lms_user.token) or {}
        totals_hist = (mcq_hist.get("totals") or {})
        consumed["mcqs_attempted"] = int(totals_hist.get("attempted") or 0)
        consumed["mcqs_correct"] = int(totals_hist.get("correct") or 0)
    except Exception as e:
        logger.warning("[plans/inclusions] mcq history failed: %s", e)

    try:
        prog = get_user_content_progress(lms_user.token) or {}
        prog_tot = prog.get("totals") or {}
        consumed["videos_watched"] = int(
            prog_tot.get("videos_watched") or prog_tot.get("videos") or 0
        )
        consumed["notes_opened"] = int(
            prog_tot.get("notes_opened") or prog_tot.get("notes") or 0
        )
    except Exception as e:
        logger.warning("[plans/inclusions] content progress failed: %s", e)

    try:
        mock_hist = get_user_mock_history(lms_user.token, limit=100) or {}
        consumed["mocks_done"] = int((mock_hist.get("totals") or {}).get("attempts") or len(mock_hist.get("mocks") or []))
    except Exception as e:
        logger.warning("[plans/inclusions] mock history failed: %s", e)

    try:
        daily = get_user_daily_activity(lms_user.token, days=14) or {}
        consumed["minutes_spent_14d"] = int(daily.get("total_minutes_window") or 0)
    except Exception as e:
        logger.warning("[plans/inclusions] daily activity failed: %s", e)

    def _pct(done: int, target: int) -> int:
        if target <= 0:
            return 0
        return max(0, min(100, int(round(done / target * 100))))

    progress = {
        "videos_pct": _pct(consumed["videos_watched"], totals["videos"]),
        "notes_pct": _pct(consumed["notes_opened"], totals["notes"]),
        "mcqs_pct": _pct(consumed["mcqs_attempted"], totals["mcqs_target"] or totals["mcq_sets"] * 10),
        "mocks_pct": _pct(consumed["mocks_done"], totals["mocks"]),
    }
    parts = [p for p in progress.values()]
    progress["overall_pct"] = int(round(sum(parts) / max(1, len(parts))))

    # ── Today's card ──
    today_iso = date.today().isoformat()
    today_day = next((d for d in days if d.get("day") == today_iso), None)
    today_block_info: Dict[str, Any] = {
        "date": today_iso,
        "minutes_planned": 0,
        "blocks": [],
        "completed_blocks": 0,
        "total_blocks": 0,
    }
    if today_day:
        blocks = today_day.get("blocks", []) or []
        today_block_info["total_blocks"] = len(blocks)
        today_block_info["minutes_planned"] = sum(int(b.get("minutes") or 0) for b in blocks)
        today_block_info["completed_blocks"] = sum(1 for b in blocks if b.get("done") or b.get("completed"))
        today_block_info["blocks"] = [
            {
                "kind": b.get("kind"),
                "title": (b.get("topic_ref") or {}).get("name") or (b.get("items") or [{}])[0].get("title") or b.get("kind"),
                "minutes": int(b.get("minutes") or 0),
                "done": bool(b.get("done") or b.get("completed")),
            }
            for b in blocks
        ]

    # Elapsed days
    start_d = plan.start_date or date.fromisoformat(days[0].get("day")) if days else None
    end_d = plan.end_date or (date.fromisoformat(days[-1].get("day")) if days else None)
    try:
        days_elapsed = max(0, (date.today() - start_d).days) if start_d else 0
    except Exception:
        days_elapsed = 0

    return {
        "has_plan": True,
        "plan_id": plan.id,
        "plan_name": plan.name,
        "start_date": start_d.isoformat() if start_d else None,
        "end_date": end_d.isoformat() if end_d else None,
        "days_total": len(days),
        "days_elapsed": days_elapsed,
        "totals": {k: v for k, v in totals.items() if k != "_ids"},
        "consumed": consumed,
        "progress": progress,
        "today": today_block_info,
    }


@router.post("/plans/generate")
def plans_generate(
    payload: GeneratePlanRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    try:
        return _plans_generate_impl(payload, lms_user, db)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error("[plans/generate] crashed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(
            500,
            f"Could not generate the plan. The LMS may be slow right now — please retry in a moment. ({type(e).__name__}: {e})"
        )


def _plans_generate_impl(
    payload: "GeneratePlanRequest",
    lms_user: LmsUser,
    db: Session,
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
            exam_date=payload.exam_date or payload.resolved_end(),
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
