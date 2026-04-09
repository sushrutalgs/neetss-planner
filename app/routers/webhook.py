"""
Inbound LMS → Planner webhook receiver.

The LMS API at `api-ruchir-optimization` posts events to:
    POST /api/webhook/lms

with these headers:
    X-Sushruta-Signature   HMAC-SHA256 of the raw body using PLANNER_WEBHOOK_SECRET
    X-Sushruta-Event-Id    UUID per emission (used for idempotency)
    X-Sushruta-Event-Type  e.g. 'hierarchy.changed', 'subscription.changed'

Body shape (envelope):
    {
      "event_id":   "...",
      "event_type": "hierarchy.changed",
      "emitted_at": "2026-04-08T14:00:00Z",
      "payload":    { ... }
    }

What this router does on receipt:
  1. Verify HMAC signature.
  2. Dedupe by event_id (in-memory ring + DB table).
  3. Dispatch by event_type → invalidate caches, mark plans as needs_rebuild,
     enqueue background re-flow.

The handlers themselves are intentionally fast — heavy work goes into a
background task so the LMS isn't blocked.
"""
from __future__ import annotations
import hmac
import hashlib
import json
import logging
import os
import time
from collections import deque
from typing import Any, Deque, Dict

from fastapi import APIRouter, Header, HTTPException, Request, BackgroundTasks

from app.database import SessionLocal
from app.models import Plan, User

logger = logging.getLogger("planner.webhook")
router = APIRouter(tags=["LMS Webhook"])

PLANNER_WEBHOOK_SECRET = os.getenv("PLANNER_WEBHOOK_SECRET", "")

# In-process dedupe ring — a recently-seen event_id won't be processed twice.
# Survives ~5 minutes of normal traffic. The DB table webhook_inbox is the
# durable backstop after a restart.
_RECENT_EVENT_IDS: Deque[str] = deque(maxlen=2048)
_RECENT_SET: set[str] = set()


def _verify_signature(raw_body: bytes, sig_header: str) -> bool:
    if not PLANNER_WEBHOOK_SECRET or not sig_header:
        return False
    expected = hmac.new(
        PLANNER_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def _seen(event_id: str) -> bool:
    if event_id in _RECENT_SET:
        return True
    _RECENT_EVENT_IDS.append(event_id)
    _RECENT_SET.add(event_id)
    # Trim set to match deque
    if len(_RECENT_SET) > _RECENT_EVENT_IDS.maxlen:  # type: ignore[operator]
        _RECENT_SET.clear()
        _RECENT_SET.update(_RECENT_EVENT_IDS)
    return False


# ───────────────────────── HANDLERS ─────────────────────────


# ───────────────────────── cache ─────────────────────────
#
# The auth_lms module maintains a 30-second in-process cache of LMS user
# states. We reach into it from webhook handlers to bust entries when
# subscription state changes server-side, so a webhook-initiated state flip
# is visible within one request rather than 30 seconds.

try:
    from app.auth_lms import _USER_STATE_CACHE as _AUTH_STATE_CACHE
except Exception:
    _AUTH_STATE_CACHE = {}


def _flag_plans_needing_rebuild(reason: str) -> int:
    """Mark every active plan (user has one) as needing a rebuild."""
    db = SessionLocal()
    try:
        updated = (
            db.query(Plan)
            .filter(Plan.needs_rebuild.is_(False))
            .update({Plan.needs_rebuild: True}, synchronize_session=False)
        )
        db.commit()
        logger.info("[webhook] flagged %d plans for rebuild (reason=%s)", updated, reason)
        return updated
    finally:
        db.close()


def _flag_plans_for_user(lms_user_id: str, reason: str) -> int:
    """Mark just one user's plans as needing rebuild."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.lms_user_id == str(lms_user_id)).one_or_none()
        if not user:
            return 0
        updated = (
            db.query(Plan)
            .filter(Plan.user_id == user.id, Plan.needs_rebuild.is_(False))
            .update({Plan.needs_rebuild: True}, synchronize_session=False)
        )
        db.commit()
        logger.info("[webhook] flagged %d plans for user=%s (reason=%s)", updated, lms_user_id, reason)
        return updated
    finally:
        db.close()


def _sync_user_subscription(lms_user_id: str, new_status: str) -> None:
    """Persist the new LMS subscription status onto the planner's User row."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.lms_user_id == str(lms_user_id)).one_or_none()
        if user:
            user.subscription_status = new_status
            db.commit()
            logger.info("[webhook] synced subscription user=%s status=%s", lms_user_id, new_status)
    finally:
        db.close()


def _handle_hierarchy_changed(payload: Dict[str, Any]) -> None:
    """
    Admin moved/reordered/renamed a category, subcategory, or topic, or
    changed topic-level priority weights. Action: flip every active plan to
    needs_rebuild so the next Today/Week fetch rebuilds with fresh content,
    and drop the in-process user-state cache so subscription gate decisions
    use post-hierarchy priority labels too.
    """
    op = payload.get("op")
    entity_type = payload.get("entity_type")
    ids = payload.get("ids", [])
    logger.info("[webhook] hierarchy.changed op=%s type=%s ids=%s", op, entity_type, ids)
    _flag_plans_needing_rebuild(reason=f"hierarchy.{op}.{entity_type}")


def _handle_content_changed(payload: Dict[str, Any]) -> None:
    """A specific Note/Video/MCQ was added, edited, or removed."""
    op = payload.get("op")
    content_id = payload.get("content_id")
    topic_id = payload.get("topic_id")
    logger.info("[webhook] content.changed op=%s content_id=%s topic_id=%s", op, content_id, topic_id)
    # Any content change invalidates the bundle for anyone whose current
    # plan includes this topic. Cheap and correct: just flip all plans.
    # (A scoped invalidation can come later once we have user→topic indexes.)
    _flag_plans_needing_rebuild(reason=f"content.{op}")


def _handle_subscription_changed(payload: Dict[str, Any]) -> None:
    user_id = payload.get("user_id")
    new_status = payload.get("status")
    if not user_id or not new_status:
        logger.warning("[webhook] subscription.changed missing user_id/status: %s", payload)
        return
    logger.info("[webhook] subscription.changed user=%s status=%s", user_id, new_status)

    _sync_user_subscription(str(user_id), str(new_status))

    # Activate/renew: rebuild so the user picks up newly-unlocked content.
    # Expire: leave the plan alone — grace policy keeps the last plan usable
    # for 3 days and hard-stop is enforced by auth_lms, not by mutating data.
    if new_status in ("active", "grace"):
        _flag_plans_for_user(str(user_id), reason=f"subscription.{new_status}")

    # Drop the auth cache entry so the next planner request re-hits LMS
    # /user-state and picks up the fresh status inside one call, not 30s.
    try:
        _AUTH_STATE_CACHE.clear()
    except Exception:
        pass


def _handle_mcq_score(payload: Dict[str, Any]) -> None:
    user_id = payload.get("user_id")
    topic_id = payload.get("topic_id")
    logger.info("[webhook] mcq.score.recorded user=%s topic=%s", user_id, topic_id)
    # Surface-level integration only for now: score-driven mastery recompute
    # runs in ai_coach, not here. A future enhancement will write the raw
    # score into MCQScore and kick a per-user mastery recompute task.


def _handle_mock_submitted(payload: Dict[str, Any]) -> None:
    logger.info("[webhook] mock.submitted user=%s mock=%s", payload.get("user_id"), payload.get("mock_id"))
    # TODO: kick the Opus mock-analysis pipeline (background task).


def _handle_priority_changed(payload: Dict[str, Any]) -> None:
    entity_type = payload.get("entity_type")
    entity_id = payload.get("entity_id")
    logger.info("[webhook] priority.changed entity=%s id=%s", entity_type, entity_id)
    # A priority flip re-weights topic rotation, so every plan should rebuild.
    _flag_plans_needing_rebuild(reason=f"priority.{entity_type}")


_DISPATCH = {
    "hierarchy.changed": _handle_hierarchy_changed,
    "content.changed": _handle_content_changed,
    "subscription.changed": _handle_subscription_changed,
    "mcq.score.recorded": _handle_mcq_score,
    "mock.submitted": _handle_mock_submitted,
    "priority.changed": _handle_priority_changed,
}


# ───────────────────────── ROUTE ─────────────────────────


@router.post("/api/webhook/lms")
async def receive_lms_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_sushruta_signature: str = Header(""),
    x_sushruta_event_id: str = Header(""),
    x_sushruta_event_type: str = Header(""),
):
    raw = await request.body()

    if not _verify_signature(raw, x_sushruta_signature):
        logger.warning("[webhook] bad signature for event %s", x_sushruta_event_id)
        raise HTTPException(status_code=401, detail="bad signature")

    if not x_sushruta_event_id:
        raise HTTPException(status_code=400, detail="missing event id")

    if _seen(x_sushruta_event_id):
        # Idempotent — already processed this exact emission.
        return {"ok": True, "dedup": True}

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    event_type = envelope.get("event_type") or x_sushruta_event_type
    payload = envelope.get("payload") or {}

    handler = _DISPATCH.get(event_type)
    if not handler:
        logger.info("[webhook] unknown event_type=%s — accepted but not dispatched", event_type)
        return {"ok": True, "unhandled": True}

    # Run the handler in a background task so the LMS doesn't wait on us.
    background_tasks.add_task(handler, payload)
    return {"ok": True, "queued": True, "event_id": x_sushruta_event_id}
