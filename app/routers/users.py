from __future__ import annotations
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.schemas import (
    UserRegister, UserLogin, TokenOut, UserOut, UserUpdate,
    ForgotPasswordRequest, ResetPasswordRequest,
)
from app.auth import (
    get_password_hash, verify_password, create_access_token,
    get_current_user, generate_reset_code,
)

router = APIRouter(tags=["Users"])


@router.post("/register", response_model=UserOut)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = models.User(
        name=payload.name, email=payload.email,
        password_hash=get_password_hash(payload.password), goal=payload.goal,
    )
    db.add(user); db.commit(); db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token({"sub": str(user.id)})
    return TokenOut(access_token=token)


@router.get("/me", response_model=UserOut)
def me(current=Depends(get_current_user)):
    return current


@router.patch("/me", response_model=UserOut)
def update_me(data: UserUpdate, db: Session = Depends(get_db), current=Depends(get_current_user)):
    if data.name is not None: current.name = data.name
    if data.goal is not None: current.goal = data.goal
    db.add(current); db.commit(); db.refresh(current)
    return current


# ==================== PASSWORD RESET ====================

@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Generate a 6-digit reset code for the user.
    Code expires in 30 minutes. Admin can view pending codes in admin panel.
    """
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        # Don't reveal whether email exists — return success either way
        return {"ok": True, "message": "If this email is registered, a reset code has been generated. Please contact support."}

    # Invalidate any previous unused codes for this user
    db.query(models.PasswordReset).filter(
        models.PasswordReset.user_id == user.id,
        models.PasswordReset.used == False,
    ).update({"used": True})

    code = generate_reset_code()
    reset = models.PasswordReset(
        user_id=user.id,
        email=user.email,
        reset_code=code,
        expires_at=datetime.utcnow() + timedelta(minutes=30),
    )
    db.add(reset); db.commit()

    return {
        "ok": True,
        "message": "Reset code generated. Please contact support to receive your code.",
    }


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Validate reset code and set new password."""
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid email or code")

    reset = (
        db.query(models.PasswordReset)
        .filter(
            models.PasswordReset.user_id == user.id,
            models.PasswordReset.reset_code == payload.code,
            models.PasswordReset.used == False,
            models.PasswordReset.expires_at > datetime.utcnow(),
        )
        .first()
    )
    if not reset:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    # Set new password
    user.password_hash = get_password_hash(payload.new_password)
    reset.used = True
    db.add(user); db.add(reset); db.commit()

    return {"ok": True, "message": "Password reset successfully. Please login."}
