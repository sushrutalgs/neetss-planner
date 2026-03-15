from __future__ import annotations
import os
import sys
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError


# -------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event("startup"))
# -------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- STARTUP ----
    try:
        from app.database import init_db
        init_db()
        print("✅ Database initialized successfully.")
    except Exception as e:
        print(f"⚠️ Database initialization failed: {e}")
    yield
    # ---- SHUTDOWN ----
    print("👋 Shutting down NEET SS Planner API.")


# -------------------------------------------------------------------
# Initialize FastAPI App
# -------------------------------------------------------------------
app = FastAPI(
    title="NEET SS Study Planner API",
    version="2.1.0",
    description="Backend for NEET SS Surgical Group Study Planner with personalization, saved plans, and user authentication.",
    lifespan=lifespan,
)


# -------------------------------------------------------------------
# Global Validation Error Handler (Debugging 400s)
# -------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    error_details = exc.errors()
    print("❌ VALIDATION ERROR →", json.dumps(error_details, indent=2), file=sys.stderr, flush=True)
    return JSONResponse(status_code=400, content={"detail": error_details})


# -------------------------------------------------------------------
# CORS Middleware (env-based origins)
# -------------------------------------------------------------------
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


# -------------------------------------------------------------------
# Static Files Setup (Frontend)
# -------------------------------------------------------------------
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print("⚠️ Static directory not found. Frontend may not load correctly.")


# -------------------------------------------------------------------
# Routers Registration
# -------------------------------------------------------------------
try:
    from app.routers import users, plans, progress, admin

    app.include_router(users.router, prefix="/api", tags=["Users"])
    app.include_router(plans.router, prefix="/api", tags=["Plans"])
    app.include_router(progress.router, prefix="/api", tags=["Progress"])
    app.include_router(admin.router)

    print("✅ Routers loaded successfully.")
except Exception as e:
    print(f"⚠️ Warning: Routers not loaded → {e}")


# -------------------------------------------------------------------
# Serve Frontend (index.html at root)
# -------------------------------------------------------------------
@app.get("/")
def serve_frontend():
    """Serve frontend UI as the homepage."""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "NEET SS Planner backend running. Frontend not found."}


@app.get("/admin")
def serve_admin_dashboard():
    """Serve private admin dashboard."""
    admin_path = os.path.join(static_dir, "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return {"message": "Admin dashboard not found."}


# -------------------------------------------------------------------
# Health Check Endpoint
# -------------------------------------------------------------------
@app.get("/health")
def health_check():
    """Simple health check for uptime monitors."""
    return {"ok": True, "message": "Planner API is healthy and online."}


# -------------------------------------------------------------------
# Local Development Entry Point
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
