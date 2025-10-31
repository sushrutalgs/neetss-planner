from __future__ import annotations
import os
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

# ✅ FIXED IMPORTS
from app.database import get_db
from app import models

router = APIRouter(prefix="/api", tags=["Auth"])

SECRET_KEY = os.getenv("JWT_SECRET", "change-this-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_TTL_MIN", "43200"))  # default 30 days

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

def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> models.User:
    """Extract user info from JWT token."""
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


# ----------------------- ROUTES -----------------------

@router.post("/register")
def register_user(username: str, password: str, db: Session = Depends(get_db)):
    """Register a new user."""
    if db.query(models.User).filter(models.User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    hashed_pw = get_password_hash(password)
    user = models.User(username=username, hashed_password=hashed_pw)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"message": "User registered successfully", "user_id": user.id}


@router.post("/login")
def login_user(username: str, password: str, db: Session = Depends(get_db)):
    """Authenticate user and return JWT token."""
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token({"sub": str(user.id)})
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def get_profile(current_user: models.User = Depends(get_current_user)):
    """Fetch current logged-in user info."""
    return {"id": current_user.id, "username": current_user.username}
