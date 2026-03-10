from __future__ import annotations
import os
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

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

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")
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

def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> models.User:
    """Extract and validate user from JWT token."""
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = int(payload.get("sub"))
    except Exception:
        raise cred_exc

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise cred_exc
    return user


# ----------------------- ADMIN GUARD -----------------------

def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Dependency that ensures the user is an admin."""
    if current_user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Access denied")
    return current_user
