"""
Federated authentication: planner accepts JWTs minted by the Sushruta LMS.

The LMS-side JWT is encrypted/signed using a secret stored in the LMS Node
backend. We do **not** decode it here directly — instead, we forward the raw
token to the LMS via /api/planner/user-state which both validates it (the
LMS middleware does the decode + Redis renewal) and returns subscription
state in one call. This:

  1. Avoids having to share Anthropic-style secrets and key rotation between
     two languages and two processes.
  2. Always reflects the freshest subscription state — no stale local
     decoding.
  3. Lets the LMS revoke a token instantly (Redis blacklist) and have the
     planner respect it within one request.

Performance: every request makes one HTTP call to the LMS. We mitigate that
with a 30-second in-process cache keyed by the token hash, since user-state
doesn't change minute-to-minute. Subscription expiry is computed against
real time so cached state is always safe.

Subscription gating policy (locked):
  active                 → allow
  grace (≤3 days expired)→ allow + add X-Sushruta-Grace header
  locked (>3 days)       → 402 Payment Required for everything except
                           /me, /plans/{id}/download.pdf, /healthz
"""
from __future__ import annotations
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status

from app.lms_client import get_user_state, LmsError

logger = logging.getLogger("planner.auth_lms")

# Module-level cache: { token_hash: (expires_at_epoch, dict) }
_USER_STATE_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_S = 30.0


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cache_get(token: str) -> Optional[dict]:
    h = _hash(token)
    entry = _USER_STATE_CACHE.get(h)
    if not entry:
        return None
    expires_at, data = entry
    if time.time() > expires_at:
        _USER_STATE_CACHE.pop(h, None)
        return None
    return data


def _cache_put(token: str, data: dict) -> None:
    _USER_STATE_CACHE[_hash(token)] = (time.time() + _CACHE_TTL_S, data)


@dataclass
class LmsUser:
    """Resolved identity for the duration of one request."""
    lms_user_id: str
    token: str
    subscription_status: str   # 'active' | 'grace' | 'locked' | 'none'
    days_to_expiry: Optional[int]
    days_since_expiry: Optional[int]
    raw_state: dict


def _normalize_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )
    # The LMS accepts both raw JWT and "Bearer <jwt>". Strip "Bearer " if present.
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return authorization.strip()


def get_lms_user(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> LmsUser:
    """
    FastAPI dependency: validates the LMS JWT and returns an LmsUser.
    Does NOT enforce subscription gating — use enforce_subscription() for that.
    """
    token = _normalize_token(authorization)

    # Cache hit?
    state = _cache_get(token)
    if state is None:
        try:
            state = get_user_state(token)
        except LmsError as e:
            logger.warning("[auth] LMS user-state failed: %s", e)
            raise HTTPException(status_code=502, detail="LMS unreachable")
        if not state or not state.get("user_id"):
            raise HTTPException(status_code=401, detail="Invalid token")
        _cache_put(token, state)

    return LmsUser(
        lms_user_id=str(state["user_id"]),
        token=token,
        subscription_status=state.get("subscription_status", "none"),
        days_to_expiry=state.get("days_to_expiry"),
        days_since_expiry=state.get("days_since_expiry"),
        raw_state=state,
    )


# Routes that bypass subscription gating regardless of state.
_GATE_BYPASS_PATHS = {
    "/healthz",
    "/api/me",
    "/api/auth/login",
    "/api/auth/register",
}


def _is_pdf_download(path: str) -> bool:
    return path.startswith("/api/plans/") and path.endswith("/download.pdf")


def enforce_subscription(
    request: Request,
    user: LmsUser = Depends(get_lms_user),
) -> LmsUser:
    """
    Enforces the 3-day grace policy. Use this on every planner endpoint
    that should be subscription-locked. PDF download stays accessible to
    expired users so they can keep working offline.
    """
    path = request.url.path
    if path in _GATE_BYPASS_PATHS or _is_pdf_download(path):
        return user

    status_str = user.subscription_status

    if status_str == "active":
        # Surface a "renewal coming up" hint when ≤7 days remain.
        if user.days_to_expiry is not None and user.days_to_expiry <= 7:
            request.state.renewal_banner = True
        return user

    if status_str == "grace":
        # Allowed, but the SPA/app should show a persistent renewal modal.
        request.state.renewal_banner = True
        return user

    if status_str == "locked":
        raise HTTPException(
            status_code=402,
            detail={
                "code": "subscription_required",
                "expired_days_ago": user.days_since_expiry,
                "renew_url": "https://sushrutalgs.in/subscription",
            },
        )

    # status == 'none' — never subscribed. Allow planner usage but in
    # chapter-reference (no LMS content) mode. The plan engine reads
    # `user.subscription_status` and degrades gracefully.
    return user
