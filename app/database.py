from __future__ import annotations
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.engine.url import make_url

# Get the database URL from environment (Heroku sets DATABASE_URL automatically)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./planner.db")

# Normalize for Heroku: convert postgres:// → postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite special case for local testing
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# Create SQLAlchemy engine
engine = create_engine(DATABASE_URL, echo=False, future=True, connect_args=connect_args)

# Configure session and base
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()


def init_db():
    from . import models
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception as e:
        print(f"⚠️ Skipped DB creation: {e}")


# Dependency injection for FastAPI routes
def get_db():
    """Provide a new SQLAlchemy session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
