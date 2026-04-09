"""
Tier 1/2/3 ML + AI surface routes.

Mounted at /api by app.main, alongside planner_v2. Everything here is
gated on enforce_subscription except the chat WebSocket which uses the
same dependency.

Routes:
  GET    /api/ai/daily-briefing          — Haiku morning briefing
  POST   /api/ai/coach/chat              — Sonnet conversational tutor
  POST   /api/ai/coach/ask               — Sonnet one-shot
  POST   /api/ai/coach/explain-weakness  — Sonnet weakness deep-dive
  POST   /api/ai/mock-debrief            — Opus post-mock analysis
  GET    /api/ai/diagnostics             — question-level diagnostics
  GET    /api/ai/snapshot/topic/{tid}    — Snapshot Series PDF (topic)
  GET    /api/ai/snapshot/weekly         — Snapshot Series PDF (week)

  GET    /api/bkt/state                  — BKT mastery state
  GET    /api/projections/runway         — time-to-mastery + bottleneck
  GET    /api/readiness                  — multi-factor readiness score
  POST   /api/readiness/what-if          — readiness sliders

  GET    /api/peer/benchmark             — cohort percentile
  GET    /api/peer/topic-strength        — relative-strength per topic
  GET    /api/peer/trending-weak         — cohort-wide trending weakness

  GET    /api/nudges                     — pending nudges for the user
  POST   /api/nudges/dismiss/{id}        — mark nudge dismissed
"""
from __future__ import annotations
import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth_lms import LmsUser, enforce_subscription
from app.database import get_db
from app.lms_client import (
    LmsError,
    get_syllabus_bundle,
    get_user_signal,
    get_user_daily_activity,
    get_user_mcq_history,
    get_user_mock_history,
    get_cohort_stats,
)
from app.models import Plan, AIRun, RecallCard, User, TopicMastery
from app.ai.bkt import batch_update_from_history, BKTParams, p_correct_next
from app.ai.projections import mastery_runway, project_days_to_mastery
from app.ai.diagnostics import build_user_diagnostic
from app.ai.tutor import chat as tutor_chat, ask as tutor_ask, explain_weakness
from app.ai.daily_briefing import generate as generate_briefing
from app.ai.mock_debrief import debrief as run_mock_debrief
from app.ai.readiness import compute as compute_readiness, what_if as readiness_what_if
from app.ai.peer import benchmark_user, topic_relative_strength, trending_weak_topics
from app.ai.revision_pdf import generate_topic_snapshot, generate_weekly_snapshot
from app.ai.mastery import build_vector as build_mastery_vector, rank_weakness, coverage_pct, avg_mastery
from app.ai.recommender import suggest_next_actions
from app.routers.planner_v2 import _get_or_create_local_user

logger = logging.getLogger("planner.routers.ml")
router = APIRouter(prefix="/api", tags=["ML/AI"])


# ───────────────────────── shared helpers ─────────────────────────


def _bundle_topic_lookup(bundle: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for cat in (bundle or {}).get("categories", []) or []:
        for sub in cat.get("subcategories", []) or []:
            for t in sub.get("topics", []) or []:
                tid = str(t.get("_id") or t.get("topic_id") or "")
                if tid:
                    out[tid] = t.get("name", "")
    return out


def _safe_signal(token: str) -> Dict[str, Any]:
    try:
        return get_user_signal(token) or {}
    except LmsError:
        return {}


def _safe_bundle(token: str) -> Dict[str, Any]:
    try:
        return get_syllabus_bundle(token) or {}
    except LmsError:
        return {}


def _flatten_bundle_topics(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for cat in (bundle or {}).get("categories", []) or []:
        for sub in cat.get("subcategories", []) or []:
            for t in sub.get("topics", []) or []:
                out.append({
                    "topic_id": str(t.get("_id") or t.get("topic_id") or ""),
                    "topic_name": t.get("name", ""),
                    "category": cat.get("name", ""),
                    "subcategory": sub.get("name", ""),
                })
    return out


def _live_vector(lms_user: LmsUser) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Dict[str, Any]]]:
    bundle = _safe_bundle(lms_user.token)
    signal = _safe_signal(lms_user.token)
    vector = build_mastery_vector(
        lms_signal=signal,
        fsrs_cards_by_topic=None,
        bundle_topics=_flatten_bundle_topics(bundle),
    )
    return bundle, signal, vector


# ════════════════════════════════════════════════════════════════
#  AI surfaces
# ════════════════════════════════════════════════════════════════


@router.get("/ai/daily-briefing")
def ai_daily_briefing(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _live_vector(lms_user)
    plan = (
        db.query(Plan)
        .filter(Plan.user_id == user.id, Plan.is_archived == False)  # noqa: E712
        .order_by(Plan.created_at.desc())
        .first()
    )
    today_iso = date.today().isoformat()
    today_blocks: List[Dict[str, Any]] = []
    if plan:
        for d in (plan.data_json or {}).get("days", []):
            if d.get("day") == today_iso:
                today_blocks = d.get("blocks", []) or []
                break

    weakest = rank_weakness(vector, top_n=5)
    streak = (signal.get("computed", {}) or {}).get("streak_current", 0)
    avg_min = (signal.get("computed", {}) or {}).get("avg_daily_minutes_14d", 0)
    days_to_exam = None
    if plan and plan.end_date:
        days_to_exam = max(0, (plan.end_date - date.today()).days)

    briefing = generate_briefing(
        user_name=user.name,
        today_blocks=today_blocks,
        weakest_topics=weakest,
        streak_current=streak,
        avg_minutes_14d=float(avg_min or 0),
        days_to_exam=days_to_exam,
    )
    return {"briefing": briefing}


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[Dict[str, str]]] = None


@router.post("/ai/coach/chat")
def ai_coach_chat(
    payload: ChatRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _live_vector(lms_user)
    weakest = rank_weakness(vector, top_n=5)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.is_archived == False).order_by(Plan.created_at.desc()).first()  # noqa: E712
    today_summary = ""
    if plan:
        today_iso = date.today().isoformat()
        for d in (plan.data_json or {}).get("days", []):
            if d.get("day") == today_iso:
                today_summary = " · ".join(b.get("title", "") for b in d.get("blocks", [])[:4])
                break

    planner_context = {
        "weakest_topics": weakest,
        "streak_current": (signal.get("computed", {}) or {}).get("streak_current", 0),
        "avg_daily_minutes_14d": (signal.get("computed", {}) or {}).get("avg_daily_minutes_14d", 0),
        "latest_predicted_rank": (signal.get("computed", {}) or {}).get("latest_predicted_rank"),
        "today_block_summary": today_summary,
        "coverage_pct": coverage_pct(vector),
        "avg_mastery": avg_mastery(vector),
    }

    result = tutor_chat(
        user_message=payload.message,
        history=payload.history or [],
        planner_context=planner_context,
    )
    # Audit
    try:
        db.add(AIRun(
            user_id=user.id,
            surface="qa_chat",
            model=result.get("model", "claude-sonnet-4-6"),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            output_md=result.get("text", "")[:65000],
        ))
        db.commit()
    except Exception:
        pass
    return result


class AskRequest(BaseModel):
    question: str


@router.post("/ai/coach/ask")
def ai_coach_ask(
    payload: AskRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _live_vector(lms_user)
    weakest = rank_weakness(vector, top_n=5)
    return tutor_ask(
        question=payload.question,
        planner_context={
            "weakest_topics": weakest,
            "streak_current": (signal.get("computed", {}) or {}).get("streak_current", 0),
            "avg_daily_minutes_14d": (signal.get("computed", {}) or {}).get("avg_daily_minutes_14d", 0),
            "coverage_pct": coverage_pct(vector),
            "avg_mastery": avg_mastery(vector),
        },
    )


class ExplainRequest(BaseModel):
    topic_id: str


@router.post("/ai/coach/explain-weakness")
def ai_coach_explain(
    payload: ExplainRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _live_vector(lms_user)
    row = vector.get(payload.topic_id) or {}
    drivers = row.get("drivers", {})
    return explain_weakness(
        topic_name=row.get("topic_name") or payload.topic_id,
        drivers=drivers,
        related_questions=[],
    )


@router.post("/ai/mock-debrief")
def ai_mock_debrief(
    mock_id: str = Query(...),
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    try:
        history = get_user_mock_history(lms_user.token, limit=30) or {}
    except LmsError as e:
        raise HTTPException(502, f"LMS unreachable: {e}")

    target = None
    for m in history.get("mocks", []) or []:
        if str(m.get("mock_id") or m.get("_id") or "") == str(mock_id):
            target = m
            break
    if not target:
        raise HTTPException(404, "mock not found in user history")

    attempts = target.get("attempts") or target.get("questions") or []
    signal = _safe_signal(lms_user.token)
    result = run_mock_debrief(target, attempts, user_signal=signal)

    try:
        db.add(AIRun(
            user_id=user.id,
            surface="mock_analysis",
            model=result.get("model", "claude-opus-4-6"),
            input_tokens=0,
            output_tokens=0,
            output_md=result.get("raw_md", "")[:65000],
        ))
        db.commit()
    except Exception:
        pass
    return result


@router.get("/ai/diagnostics")
def ai_diagnostics(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    try:
        since = (date.today() - timedelta(days=30)).isoformat()
        history = get_user_mcq_history(lms_user.token, since_iso=since) or {}
    except LmsError as e:
        raise HTTPException(502, f"LMS unreachable: {e}")
    attempts = history.get("attempts") or history.get("rows") or history.get("by_topic") or []
    if not isinstance(attempts, list):
        attempts = []
    bundle = _safe_bundle(lms_user.token)
    return build_user_diagnostic(attempts, topic_lookup=_bundle_topic_lookup(bundle))


@router.get("/ai/snapshot/topic/{topic_id}")
def ai_snapshot_topic(
    topic_id: str,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _live_vector(lms_user)
    row = vector.get(topic_id) or {}
    snap = generate_topic_snapshot(
        user_id=user.id,
        topic_name=row.get("topic_name") or topic_id,
        note_chunks=[],
        mastery_drivers=row.get("drivers", {}),
    )
    if os.path.exists(snap["path"]):
        return FileResponse(snap["path"], media_type="application/pdf", filename=snap["filename"])
    raise HTTPException(500, "snapshot generation failed")


@router.get("/ai/snapshot/weekly")
def ai_snapshot_weekly(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.is_archived == False).order_by(Plan.created_at.desc()).first()  # noqa: E712
    week_days = []
    if plan:
        today_iso = date.today().isoformat()
        end = (date.today() + timedelta(days=7)).isoformat()
        week_days = [d for d in (plan.data_json or {}).get("days", []) if today_iso <= d.get("day", "") <= end]
    snap = generate_weekly_snapshot(user_id=user.id, week_days=week_days, weekly_signal=_safe_signal(lms_user.token))
    if os.path.exists(snap["path"]):
        return FileResponse(snap["path"], media_type="application/pdf", filename=snap["filename"])
    raise HTTPException(500, "snapshot generation failed")


# ════════════════════════════════════════════════════════════════
#  BKT + projections + readiness
# ════════════════════════════════════════════════════════════════


@router.get("/bkt/state")
def bkt_state(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    try:
        history = get_user_mcq_history(lms_user.token) or {}
    except LmsError as e:
        raise HTTPException(502, f"LMS unreachable: {e}")
    rows = history.get("by_topic") or history.get("rows") or []
    p_known = batch_update_from_history(rows)
    out = {}
    for tid, p in p_known.items():
        params = BKTParams()
        out[tid] = {
            "p_known": round(p, 4),
            "p_correct_next": round(p_correct_next(p, params), 4),
        }
    return {"state": out, "n_topics": len(out)}


@router.get("/projections/runway")
def projections_runway(
    target: float = Query(0.9, ge=0.5, le=0.99),
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.is_archived == False).order_by(Plan.created_at.desc()).first()  # noqa: E712
    if not plan or not plan.end_date:
        raise HTTPException(404, "no active plan with end_date")

    try:
        history = get_user_mcq_history(lms_user.token) or {}
        daily = get_user_daily_activity(lms_user.token, days=14) or {}
    except LmsError as e:
        raise HTTPException(502, f"LMS unreachable: {e}")

    p_known = batch_update_from_history(history.get("by_topic") or history.get("rows") or [])
    avg_min = float((daily or {}).get("avg_minutes_last_14d", 0) or 0)
    # Assume 90s per MCQ → throughput per day.
    daily_mcq_throughput = (avg_min * 60.0) / 90.0
    return mastery_runway(
        p_known_by_topic=p_known,
        end_date=plan.end_date,
        daily_mcq_throughput=daily_mcq_throughput,
        target=target,
    )


@router.get("/readiness")
def readiness(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    bundle, signal, vector = _live_vector(lms_user)

    plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.is_archived == False).order_by(Plan.created_at.desc()).first()  # noqa: E712
    days_remaining = 0
    if plan and plan.end_date:
        days_remaining = max(0, (plan.end_date - date.today()).days)

    try:
        daily = get_user_daily_activity(lms_user.token, days=14) or {}
        mocks = get_user_mock_history(lms_user.token, limit=5) or {}
    except LmsError:
        daily = {}
        mocks = {}

    avg_min_14d = float((daily or {}).get("avg_minutes_last_14d", 0) or 0)
    recent_mocks = mocks.get("mocks", []) or []
    if recent_mocks:
        recent_acc = sum((m.get("accuracy_pct") or 0) for m in recent_mocks[:5]) / max(1, len(recent_mocks[:5]))
    else:
        recent_acc = 0.0

    # Recall health = 1 - (overdue / total_active)
    try:
        total_cards = db.query(RecallCard).filter(RecallCard.user_id == user.id).count() or 1
        overdue = (
            db.query(RecallCard)
            .filter(RecallCard.user_id == user.id, RecallCard.next_review_date <= date.today())
            .count()
        )
        recall_health = max(0.0, 1.0 - overdue / total_cards)
    except Exception:
        recall_health = 1.0

    # Days required = bottleneck from BKT projection (best-effort)
    try:
        history = get_user_mcq_history(lms_user.token) or {}
        p_known = batch_update_from_history(history.get("by_topic") or history.get("rows") or [])
        runway = mastery_runway(p_known, plan.end_date if plan and plan.end_date else date.today() + timedelta(days=180), max(1.0, (avg_min_14d * 60) / 90), target=0.9)
        days_required = runway.get("days_required_bottleneck", 0)
    except Exception:
        days_required = 0

    score = compute_readiness(
        avg_mastery=avg_mastery(vector),
        coverage_pct=coverage_pct(vector),
        recent_mock_accuracy_pct=recent_acc,
        avg_minutes_14d=avg_min_14d,
        recall_health_pct=recall_health,
        days_remaining=days_remaining,
        days_required_bottleneck=days_required,
    )
    return score


class WhatIfRequest(BaseModel):
    factors: Dict[str, float]


@router.post("/readiness/what-if")
def readiness_whatif(
    payload: WhatIfRequest,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    base = readiness(lms_user=lms_user, db=db)
    return readiness_what_if(base, payload.factors)


# ════════════════════════════════════════════════════════════════
#  Peer benchmarking
# ════════════════════════════════════════════════════════════════


@router.get("/peer/benchmark")
def peer_benchmark(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    if not user.leaderboard_opt_in:
        raise HTTPException(403, "Enable leaderboard opt-in to use peer benchmarking")
    try:
        cohort = get_cohort_stats(lms_user.token, exam_type=user.exam_type or "NEET_SS") or {}
    except LmsError as e:
        raise HTTPException(502, f"LMS unreachable: {e}")

    bundle, signal, vector = _live_vector(lms_user)
    try:
        daily = get_user_daily_activity(lms_user.token, days=14) or {}
        mocks = get_user_mock_history(lms_user.token, limit=5) or {}
    except LmsError:
        daily, mocks = {}, {}

    recent_mocks = mocks.get("mocks", []) or []
    user_metrics = {
        "mastery_avg": avg_mastery(vector),
        "coverage_pct": coverage_pct(vector),
        "mock_accuracy": sum((m.get("accuracy_pct") or 0) for m in recent_mocks[:5]) / max(1, len(recent_mocks[:5])) if recent_mocks else 0,
        "avg_min_14d": (daily or {}).get("avg_minutes_last_14d", 0),
    }
    return benchmark_user(user_metrics, cohort.get("distributions", {}))


@router.get("/peer/topic-strength")
def peer_topic_strength(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    if not user.leaderboard_opt_in:
        raise HTTPException(403, "Enable leaderboard opt-in to use peer benchmarking")
    try:
        cohort = get_cohort_stats(lms_user.token, exam_type=user.exam_type or "NEET_SS") or {}
    except LmsError as e:
        raise HTTPException(502, f"LMS unreachable: {e}")
    bundle, signal, vector = _live_vector(lms_user)
    return {"topics": topic_relative_strength(vector, cohort.get("topic_means", {}))}


@router.get("/peer/trending-weak")
def peer_trending_weak(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    if not user.leaderboard_opt_in:
        raise HTTPException(403, "Enable leaderboard opt-in to use peer benchmarking")
    try:
        cohort = get_cohort_stats(lms_user.token, exam_type=user.exam_type or "NEET_SS") or {}
    except LmsError as e:
        raise HTTPException(502, f"LMS unreachable: {e}")
    return {"topics": trending_weak_topics(cohort.get("topic_trends", []))}


# ════════════════════════════════════════════════════════════════
#  Nudges
# ════════════════════════════════════════════════════════════════


@router.get("/nudges")
def list_nudges(
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    rows = (
        db.query(AIRun)
        .filter(AIRun.user_id == user.id, AIRun.surface == "nudge")
        .order_by(AIRun.created_at.desc())
        .limit(10)
        .all()
    )
    out = []
    for r in rows:
        try:
            out.append({"id": r.id, "created_at": r.created_at.isoformat(), **json.loads(r.output_md or "{}")})
        except Exception:
            pass
    return {"nudges": out}


@router.post("/nudges/dismiss/{nudge_id}")
def dismiss_nudge(
    nudge_id: int,
    lms_user: LmsUser = Depends(enforce_subscription),
    db: Session = Depends(get_db),
):
    user = _get_or_create_local_user(db, lms_user)
    rec = db.query(AIRun).filter(AIRun.id == nudge_id, AIRun.user_id == user.id).first()
    if rec:
        rec.error = "dismissed"
        db.commit()
    return {"ok": True}
