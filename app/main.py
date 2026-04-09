from __future__ import annotations
import os
import sys
import json
from contextlib import asynccontextmanager

# Load .env before any other imports that read env vars (database URL,
# LMS_BASE_URL, ANTHROPIC_API_KEY, PLANNER_WEBHOOK_SECRET, JWT_SECRET).
# The .env file lives at the project root alongside this `app/` package.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    # python-dotenv not installed yet on first deploy; pm2 env vars still work
    pass

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.database import init_db
        init_db()
        print("✅ Database initialized successfully.")
    except Exception as e:
        print(f"⚠️ Database initialization failed: {e}")
    yield
    print("👋 Shutting down Cortex Surgery Planner API.")


app = FastAPI(
    title="Cortex Surgery AI Planner API",
    version="3.0.0",
    description="NEET SS Surgical Group Study Planner with adaptive scheduling, SM-2 recall, analytics, AI coaching, and peer leaderboard.",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_details = exc.errors()
    print("❌ VALIDATION ERROR →", json.dumps(error_details, indent=2), file=sys.stderr, flush=True)
    return JSONResponse(status_code=400, content={"detail": error_details})


# CORS
allowed_origins_str = os.getenv("ALLOWED_ORIGINS", "*")
if allowed_origins_str == "*":
    allowed_origins = ["*"]
else:
    allowed_origins = [o.strip() for o in allowed_origins_str.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static Files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Routers
try:
    from app.routers import (
        users, plans, progress, admin,
        mcq_scores, study_sessions, notes, recall,
        analytics, leaderboard, ai_coach, dashboard,
        webhook, lms_content, planner_v2,
    )

    # Phase 1 — federated content-aware planner consumed by Flutter + new SPA.
    # MUST be registered BEFORE users.router so the LMS-aware /api/me wins
    # the first-match lookup (users.py also defines a legacy /api/me that
    # validates planner-local JWTs and breaks LMS-federated auth).
    app.include_router(planner_v2.router)
    app.include_router(ai_coach.router, prefix="/api", tags=["AI Coach"])

    app.include_router(dashboard.router, prefix="/api", tags=["Dashboard"])
    app.include_router(users.router, prefix="/api", tags=["Users"])
    app.include_router(plans.router, prefix="/api", tags=["Plans"])
    app.include_router(progress.router, prefix="/api", tags=["Progress"])
    app.include_router(mcq_scores.router, prefix="/api", tags=["MCQ Scores"])
    app.include_router(study_sessions.router, prefix="/api", tags=["Study Sessions"])
    app.include_router(notes.router, prefix="/api", tags=["Notes"])
    app.include_router(recall.router, prefix="/api", tags=["Recall"])
    app.include_router(analytics.router, prefix="/api", tags=["Analytics"])
    app.include_router(leaderboard.router, prefix="/api", tags=["Leaderboard"])
    app.include_router(webhook.router)
    app.include_router(lms_content.router)
    app.include_router(admin.router)

    print("✅ All routers loaded successfully.")
except Exception as e:
    print(f"⚠️ Warning: Routers not loaded → {e}")


@app.get("/")
def serve_frontend():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Cortex Surgery Planner backend running. Frontend not found."}


@app.get("/admin")
def serve_admin_dashboard():
    admin_path = os.path.join(static_dir, "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return {"message": "Admin dashboard not found."}


@app.get("/v2")
def serve_v2_spa():
    """New AI-driven planner SPA. Consumes planner_v2 routes + LMS federation."""
    v2_path = os.path.join(static_dir, "v2.html")
    if os.path.exists(v2_path):
        return FileResponse(v2_path)
    return {"message": "v2 SPA not found."}


@app.get("/health")
def health_check():
    return {"ok": True, "version": "3.0.0", "message": "Cortex Surgery Planner is healthy."}


# PWA manifest
@app.get("/manifest.json")
def serve_manifest():
    manifest_path = os.path.join(static_dir, "manifest.json")
    if os.path.exists(manifest_path):
        return FileResponse(manifest_path, media_type="application/json")
    return JSONResponse({
        "name": "Cortex Surgery AI Planner",
        "short_name": "Cortex",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f5f5f7",
        "theme_color": "#0071e3",
        "icons": [{"src": "/static/logo.png", "sizes": "512x512", "type": "image/png"}],
    })


@app.get("/sw.js")
def serve_sw():
    sw_path = os.path.join(static_dir, "sw.js")
    if os.path.exists(sw_path):
        return FileResponse(sw_path, media_type="application/javascript")
    return Response(content="// No service worker", media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
