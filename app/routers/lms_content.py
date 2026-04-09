"""
LMS content surface for the planner.

Three endpoints exposed to the SPA + Flutter app:

  GET /api/lms/bundle      — full content tree the user can access (proxy + cache)
  GET /api/lms/today       — today's day card with content tiles already filled
  POST /api/lms/refresh    — force-bust the bundle cache for this user

These are the endpoints the existing planner web SPA will call to render
content-aware day cards. The Flutter app will use the same endpoints later.
"""
from __future__ import annotations
import time
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.auth_lms import get_lms_user, enforce_subscription, LmsUser
from app.lms_client import get_syllabus_bundle, LmsError

logger = logging.getLogger("planner.lms_content")
router = APIRouter(tags=["LMS Content"])


# In-process cache: { lms_user_id: (expires_at_epoch, bundle_dict) }
# 15-minute TTL, busted on webhook receipt or explicit refresh.
_BUNDLE_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_BUNDLE_TTL_S = 15 * 60


def _bundle_cache_get(uid: str) -> Optional[Dict[str, Any]]:
    entry = _BUNDLE_CACHE.get(uid)
    if not entry:
        return None
    expires_at, data = entry
    if time.time() > expires_at:
        _BUNDLE_CACHE.pop(uid, None)
        return None
    return data


def _bundle_cache_put(uid: str, data: Dict[str, Any]) -> None:
    _BUNDLE_CACHE[uid] = (time.time() + _BUNDLE_TTL_S, data)


def invalidate_bundle_cache(lms_user_id: Optional[str] = None) -> None:
    """Called by the webhook handler when LMS hierarchy/content changes."""
    if lms_user_id:
        _BUNDLE_CACHE.pop(lms_user_id, None)
    else:
        _BUNDLE_CACHE.clear()


def _fetch_bundle(user: LmsUser) -> Dict[str, Any]:
    cached = _bundle_cache_get(user.lms_user_id)
    if cached is not None:
        return cached
    try:
        bundle = get_syllabus_bundle(user.token)
    except LmsError as e:
        logger.warning("[lms_content] bundle fetch failed for %s: %s", user.lms_user_id, e)
        raise HTTPException(status_code=502, detail=f"LMS unreachable: {e}")
    _bundle_cache_put(user.lms_user_id, bundle)
    return bundle


@router.get("/api/lms/bundle")
def get_bundle(user: LmsUser = Depends(enforce_subscription)):
    """Full subscribed-content tree. Used by the SPA on first load."""
    return _fetch_bundle(user)


@router.post("/api/lms/refresh")
def refresh_bundle(user: LmsUser = Depends(get_lms_user)):
    """Force a fresh fetch — used after a user knows admin made changes."""
    invalidate_bundle_cache(user.lms_user_id)
    bundle = _fetch_bundle(user)
    return {"refreshed": True, "category_count": len(bundle.get("categories", []))}


# ───────────────────────── content-aware day card ─────────────────────────


def _flatten_topics(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten the bundle into a list of topics with their parent context."""
    out: List[Dict[str, Any]] = []
    for cat in bundle.get("categories", []):
        for sub in cat.get("subcategories", []):
            for topic in sub.get("topics", []):
                out.append({
                    "topic": topic,
                    "subcategory_name": sub.get("name"),
                    "category_name": cat.get("name"),
                    "priority_label": topic.get("priority_label"),
                    "priority_color": topic.get("priority_color"),
                })
    return out


def _build_day_blocks(
    topics: List[Dict[str, Any]],
    time_budget_min: int,
    day_index: int,
) -> List[Dict[str, Any]]:
    """
    Greedy block-filler. Picks topics in priority + recency order, then
    schedules read → watch → practice within the day's time budget.

    This is the v1 of the content-aware scheduler. The full ML-driven
    version (mastery score, weakness boost) lands in Phase 4. For now we:
      - rotate topics by day_index so users see variety
      - prefer P1 priority topics
      - pick the smallest items that fit, never exceed budget
    """
    blocks: List[Dict[str, Any]] = []
    remaining = time_budget_min
    if not topics:
        return blocks

    # Stable rotation: each day starts at a different topic
    n = len(topics)
    rotated = topics[day_index % n :] + topics[: day_index % n]

    # Prefer priority P1 (red/orange) ahead of P3 (grey/blue)
    def prio_rank(t: Dict[str, Any]) -> int:
        label = (t.get("priority_label") or "").lower()
        if "p1" in label or "high" in label:
            return 0
        if "p2" in label or "moderate" in label:
            return 1
        return 2

    rotated.sort(key=prio_rank)

    for entry in rotated:
        if remaining < 15:
            break
        topic = entry["topic"]
        topic_ref = {
            "lms_topic_id": topic["_id"],
            "name": topic["name"],
            "category_name": entry["category_name"],
            "subcategory_name": entry["subcategory_name"],
            "priority_label": entry.get("priority_label"),
            "priority_color": entry.get("priority_color"),
        }
        est = topic.get("est_minutes") or {}
        cids = topic.get("content_ids") or {}
        counts = topic.get("content_counts") or {}

        # READ block — first available note
        if cids.get("notes") and est.get("read", 0) > 0 and remaining >= 10:
            spend = min(est["read"], max(15, int(remaining * 0.4)))
            blocks.append({
                "kind": "read",
                "topic_ref": topic_ref,
                "items": [{
                    "content_kind": "notes",
                    "content_id": cids["notes"][0],
                    "title": f"{topic['name']} — Notes",
                    "est_min": spend,
                }],
                "rationale": f"{topic_ref['priority_label'] or 'Topic'} reading slot",
            })
            remaining -= spend

        # WATCH block — first available video
        if cids.get("videos") and est.get("watch", 0) > 0 and remaining >= 10:
            spend = min(est["watch"], max(10, int(remaining * 0.35)))
            blocks.append({
                "kind": "watch",
                "topic_ref": topic_ref,
                "items": [{
                    "content_kind": "video",
                    "content_id": cids["videos"][0],
                    "title": f"{topic['name']} — Video",
                    "est_min": spend,
                }],
                "rationale": "Visual reinforcement",
            })
            remaining -= spend

        # PRACTICE block — MCQ set (server picks at runtime)
        if counts.get("mcqs", 0) > 0 and remaining >= 15:
            mcq_count = min(30, counts["mcqs"], max(10, remaining // 2))
            spend = int(mcq_count * 1.5)
            blocks.append({
                "kind": "practice",
                "topic_ref": topic_ref,
                "items": [{
                    "content_kind": "mcq_set",
                    "topic_id": topic["_id"],
                    "title": f"{topic['name']} — {mcq_count} MCQs",
                    "count": mcq_count,
                    "est_min": spend,
                    "fetch_url": f"/api/lms/mcq-batch?topic_id={topic['_id']}&count={mcq_count}",
                }],
                "rationale": "Active recall",
                "target_accuracy": 70,
            })
            remaining -= spend

        if len(blocks) >= 6:
            break

    return blocks


@router.get("/api/lms/today")
def get_today(
    hours: float = 4.0,
    user: LmsUser = Depends(enforce_subscription),
):
    """
    Returns today's content-aware day card. Reads the cached bundle and
    runs the greedy block-filler to produce a list of read/watch/practice
    blocks that fit the user's daily hours budget.

    Query params:
      hours — daily study budget (default 4)
    """
    bundle = _fetch_bundle(user)
    topics = _flatten_topics(bundle)
    sub = bundle.get("subscription") or {}
    if not sub.get("active") or not topics:
        return {
            "date": str(date.today()),
            "subscription_active": bool(sub.get("active")),
            "blocks": [],
            "message": "No subscribed content found — your plan is running in chapter-reference mode.",
        }

    # Stable day index: days since user's earliest subscription start
    day_index = (date.today() - date(2020, 1, 1)).days
    budget = int(hours * 60)
    blocks = _build_day_blocks(topics, budget, day_index)

    total_min = sum(
        item.get("est_min", 0)
        for b in blocks
        for item in b.get("items", [])
    )

    return {
        "date": str(date.today()),
        "subscription_active": True,
        "subscription_status": user.subscription_status,
        "days_since_expiry": user.days_since_expiry,
        "time_budget_min": budget,
        "scheduled_min": total_min,
        "blocks": blocks,
    }


@router.get("/api/lms/mcq-batch")
def get_mcq_batch(
    topic_id: str,
    count: int = 30,
    user: LmsUser = Depends(enforce_subscription),
):
    """Proxy to LMS mcq-batch — keeps the planner SPA from talking to LMS directly."""
    from app.lms_client import get_mcq_batch as lms_mcq
    try:
        return lms_mcq(user.token, topic_id, count=count)
    except LmsError as e:
        raise HTTPException(status_code=502, detail=str(e))
