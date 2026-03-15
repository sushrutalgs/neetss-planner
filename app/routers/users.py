from __future__ import annotations
import random
import time
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional

from app.database import get_db
from app import models
from app.schemas import UserRegister, UserLogin, TokenOut, UserOut, UserUpdate
from app.auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
)

router = APIRouter(tags=["Users"])

# ── In-memory reset code store: {email: {"code": "123456", "expires": timestamp}} ──
_reset_codes = {}


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str


@router.post("/register", response_model=UserOut)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = models.User(
        name=payload.name,
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        goal=payload.goal,
        exam_type=payload.exam_type,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token({"sub": str(user.id)})
    return TokenOut(access_token=token)


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Generate a 6-digit reset code. Logs to server console (email integration pending)."""
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    # Always return success to prevent email enumeration
    if not user:
        return {"message": "If this email is registered, a reset code has been sent."}

    code = str(random.randint(100000, 999999))
    _reset_codes[payload.email] = {
        "code": code,
        "expires": time.time() + 600,  # 10 minutes
    }
    # Log to server console (replace with email sending later)
    print(f"🔑 PASSWORD RESET CODE for {payload.email}: {code}", flush=True)

    return {"message": "If this email is registered, a reset code has been sent."}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Verify reset code and set new password."""
    stored = _reset_codes.get(payload.email)
    if not stored:
        raise HTTPException(status_code=400, detail="No reset code found. Request a new one.")
    if time.time() > stored["expires"]:
        _reset_codes.pop(payload.email, None)
        raise HTTPException(status_code=400, detail="Reset code expired. Request a new one.")
    if stored["code"] != payload.code.strip():
        raise HTTPException(status_code=400, detail="Invalid reset code.")

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if len(payload.new_password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters.")

    user.password_hash = get_password_hash(payload.new_password)
    db.commit()
    _reset_codes.pop(payload.email, None)

    return {"message": "Password reset successfully. You can now sign in."}


@router.get("/me", response_model=UserOut)
def me(current=Depends(get_current_user)):
    return current


@router.patch("/me", response_model=UserOut)
def update_me(data: UserUpdate, db: Session = Depends(get_db), current=Depends(get_current_user)):
    if data.name is not None:
        current.name = data.name
    if data.goal is not None:
        current.goal = data.goal
    if data.exam_type is not None:
        current.exam_type = data.exam_type
    if data.leaderboard_opt_in is not None:
        current.leaderboard_opt_in = data.leaderboard_opt_in
    db.add(current)
    db.commit()
    db.refresh(current)
    return current
