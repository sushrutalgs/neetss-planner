from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

logger = logging.getLogger("planner.auth")

# ----------------------- CONFIG -----------------------

SECRET_KEY = os.getenv("JWT_SECRET", "")
if not SECRET_KEY:
    import warnings
    warnings.warn(
        "⚠️ JWT_SECRET not set! Using fallback. Set JWT_SECRET env var in production.",
        stacklevel=2,
    )
    SECRET_KEY = "dev-fallback-change-this-in-production"

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_TTL_MIN", "43200"))  # default 30 days

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "sushrutalgs@gmail.com")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ----------------------- PASSWORD HELPERS -----------------------

def get_password_hash(password: str) -> str:
    """Hash a plaintext password."""
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify that a plaintext password matches the hashed version."""
    return pwd_context.verify(password, hashed)


# ----------------------- TOKEN GENERATION -----------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token with expiry."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ----------------------- DEPENDENCY: GET CURRENT USER -----------------------

def _resolve_lms_user(db: Session, token: str) -> Optional[models.User]:
    """
    Fall-through: treat the token as an LMS-federated JWT. Forwards it to
    the LMS /api/planner/user-state endpoint (via app.auth_lms), then maps
    the resulting lms_user_id to a local planner User row. Lazily provisions
    a row on first sight (same logic as planner_v2._get_or_create_local_user)
    so legacy routers (analytics, progress, recall, leaderboard, mcq-scores,
    study-sessions, notes, dashboard) transparently accept LMS tokens
    without any per-router edits.
    """
    try:
        # Lazy import to avoid circulars at module load (auth_lms imports
        # lms_client which imports httpx).
        from app.auth_lms import get_user_state_cached, LmsError  # type: ignore
    except ImportError:
        from app.auth_lms import LmsError  # fallback for older module
        from app.lms_client import get_user_state as _raw_state

        def get_user_state_cached(t: str) -> dict:  # type: ignore
            return _raw_state(t)

    try:
        state = get_user_state_cached(token)
    except LmsError as e:
        logger.warning("[auth] LMS fall-through failed: %s", e)
        return None
    except Exception as e:
        logger.warning("[auth] LMS fall-through errored: %s", e)
        return None

    if not state or not state.get("user_id"):
        return None

    lms_user_id = str(state["user_id"])
    raw = state if isinstance(state, dict) else {}

    user = (
        db.query(models.User)
        .filter(models.User.lms_user_id == lms_user_id)
        .one_or_none()
    )
    if user:
        return user

    email = raw.get("email")
    # Link legacy planner-only row by email if one exists
    if email:
        legacy = (
            db.query(models.User)
            .filter(models.User.email == email, models.User.lms_user_id.is_(None))
            .one_or_none()
        )
        if legacy:
            legacy.lms_user_id = lms_user_id
            legacy.subscription_status = raw.get("subscription_status", "none")
            db.commit()
            return legacy

    user = models.User(
        name=raw.get("name") or "Planner User",
        email=email or f"{lms_user_id}@lms.local",
        password_hash=None,
        lms_user_id=lms_user_id,
        subscription_status=raw.get("subscription_status", "none"),
        last_lms_sync_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_scheme),
) -> models.User:
    """
    Dual-mode auth dependency.

    1. If the Authorization header carries a planner-local JWT (signed with
       our JWT_SECRET), decode it and load by `users.id` — the original
       behaviour, preserved so the legacy cortexsurgery.ai email/password
       flow still works.
    2. Otherwise fall through to LMS federation: forward the raw token to
       the LMS, resolve the lms_user_id, map to a local row (lazy-create).

    This unifies identity across the v2 SPA (LMS JWT), the Flutter app (LMS
    JWT), and any remaining legacy web login (planner-local JWT) without
    touching every router.
    """
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # OAuth2PasswordBearer with auto_error=False may hand us None — in that
    # case try the raw Authorization header ourselves so Bearer-prefixed
    # tokens minted by the LMS still reach this dependency when the SPA
    # doesn't follow the OAuth2 password grant convention.
    if not token:
        auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise cred_exc

    # ── Path 1: planner-local JWT ──
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is not None:
            try:
                user_id = int(sub)
            except (TypeError, ValueError):
                user_id = None
            if user_id is not None:
                user = db.query(models.User).filter(models.User.id == user_id).first()
                if user:
                    return user
    except jwt.InvalidTokenError:
        pass  # fall through to LMS
    except Exception as e:
        logger.debug("[auth] local JWT decode errored: %s", e)

    # ── Path 2: LMS-federated JWT ──
    user = _resolve_lms_user(db, token)
    if user:
        return user

    raise cred_exc


# ----------------------- ADMIN GUARD -----------------------

def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Dependency that ensures the user is an admin."""
    if current_user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Access denied")
    return current_user
