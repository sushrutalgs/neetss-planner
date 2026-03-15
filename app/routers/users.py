from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.schemas import UserRegister, UserLogin, TokenOut, UserOut, UserUpdate
from app.auth import (
    get_password_hash, verify_password, create_access_token, get_current_user,
)

router = APIRouter(tags=["Users"])


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
