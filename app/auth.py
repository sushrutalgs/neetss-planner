from __future__ import annotations
import os, random, string
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

SECRET_KEY = os.getenv("JWT_SECRET", "")
if not SECRET_KEY:
    import warnings
    warnings.warn("⚠️ JWT_SECRET not set! Using fallback.", stacklevel=2)
    SECRET_KEY = "dev-fallback-change-this-in-production"

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_TTL_MIN", "43200"))
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "sushrutalgs@gmail.com")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def generate_reset_code() -> str:
    """Generate a 6-digit numeric reset code."""
    return ''.join(random.choices(string.digits, k=6))

def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> models.User:
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

def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Access denied")
    return current_user
