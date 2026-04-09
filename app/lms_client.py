"""
Cortex Surgery Planner ↔ Sushruta LMS API client.

Single place that talks to the LMS Node API. Other planner modules import
from here so the contract surface is small and well-known.

Endpoints consumed:
  GET /api/planner/syllabus-bundle   — full content tree for the user
  GET /api/planner/user-state        — subscription/state probe
  GET /api/planner/mcq-batch         — server-picked MCQs for a topic

The planner forwards the user's LMS JWT (received in the Authorization
header) directly to the LMS — no separate identity. Federation is via
the shared JWT secret, so any token the LMS minted is also accepted by
the planner via verify_lms_token().
"""
from __future__ import annotations
import os
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("planner.lms_client")

LMS_BASE = os.getenv("LMS_BASE_URL", "").rstrip("/")
LMS_TIMEOUT_S = float(os.getenv("LMS_TIMEOUT_S", "10"))


class LmsError(Exception):
    """Raised on LMS API failures."""


def _client() -> httpx.Client:
    if not LMS_BASE:
        raise LmsError("LMS_BASE_URL not configured")
    return httpx.Client(base_url=LMS_BASE, timeout=LMS_TIMEOUT_S)


def _headers(token: str) -> Dict[str, str]:
    # The LMS middleware accepts both raw and "Bearer <token>" — we send raw
    # to match the way the rest of the LMS clients (admin SPA, Flutter app)
    # already use it.
    return {"authorization": token}


def get_syllabus_bundle(token: str) -> Dict[str, Any]:
    """Fetch the full hierarchical content tree the authed user can access."""
    try:
        with _client() as c:
            r = c.get("/api/planner/syllabus-bundle", headers=_headers(token))
        if r.status_code != 200:
            raise LmsError(f"syllabus-bundle http {r.status_code}: {r.text[:300]}")
        body = r.json()
        # The LMS wraps every response in { status, data } via success() helper.
        return body.get("data", body)
    except httpx.HTTPError as e:
        raise LmsError(f"syllabus-bundle network: {e}") from e


def get_user_state(token: str) -> Dict[str, Any]:
    """Lightweight subscription probe — used by the polling reconciler."""
    try:
        with _client() as c:
            r = c.get("/api/planner/user-state", headers=_headers(token))
        if r.status_code != 200:
            raise LmsError(f"user-state http {r.status_code}: {r.text[:300]}")
        body = r.json()
        return body.get("data", body)
    except httpx.HTTPError as e:
        raise LmsError(f"user-state network: {e}") from e


def get_mcq_batch(
    token: str,
    topic_id: str,
    count: int = 30,
    exclude_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Server-picked MCQ batch for a topic."""
    params: Dict[str, Any] = {"topic_id": topic_id, "count": count}
    if exclude_ids:
        params["exclude"] = ",".join(exclude_ids)
    try:
        with _client() as c:
            r = c.get("/api/planner/mcq-batch", headers=_headers(token), params=params)
        if r.status_code != 200:
            raise LmsError(f"mcq-batch http {r.status_code}: {r.text[:300]}")
        body = r.json()
        return body.get("data", body)
    except httpx.HTTPError as e:
        raise LmsError(f"mcq-batch network: {e}") from e
