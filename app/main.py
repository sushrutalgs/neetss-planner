from __future__ import annotations
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .routers import users, plans, progress
from fastapi.staticfiles import StaticFiles

# Serve static frontend
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

app = FastAPI(
    title="NEET SS Study Planner API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS (open; tighten for prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(users.router, prefix="/api")
app.include_router(plans.router, prefix="/api")
app.include_router(progress.router, prefix="/api")

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/health")
def health():
    return {"ok": True}
