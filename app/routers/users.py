from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

# ✅ FIXED IMPORTS
from app.database import get_db
from app import models
from app.schemas import UserRegister, UserLogin, TokenOut, UserOut, UserUpdate
from app.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/api", tags=["Users"])

# ------------------------ REGISTER USER ------------------------

@router.post("/register", response_model=UserOut)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    """Register a new user (name, email, password, and goal)."""
    if db.query(models.User).filter(models.User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = models.User(
        name=payload.name,
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        goal=payload.goal,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ------------------------ LOGIN USER ------------------------

@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Authenticate user and issue JWT token."""
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    token = create_access_token({"sub": str(user.id)})
    return TokenOut(access_token=token)


# ------------------------ GET PROFILE ------------------------

@router.get("/me", response_model=UserOut)
def me(current=Depends(get_current_user)):
    """Get details of the currently logged-in user."""
    return current


# ------------------------ UPDATE PROFILE ------------------------

@router.patch("/me", response_model=UserOut)
def update_me(
    data: UserUpdate,
    db: Session = Depends(get_db),
    current=Depends(get_current_user),
):
    """Update name or goal for the current user."""
    if data.name is not None:
        current.name = data.name
    if data.goal is not None:
        current.goal = data.goal

    db.add(current)
    db.commit()
    db.refresh(current)
    return current
