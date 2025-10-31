from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

# -------------------------------------------------------------------
# Initialize FastAPI App
# -------------------------------------------------------------------
app = FastAPI(
    title="NEET SS Study Planner API",
    version="2.0.0",
    description="Backend for NEET SS Surgical Group Study Planner with personalization, saved plans, and user authentication."
)

# -------------------------------------------------------------------
# CORS Middleware
# -------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Later restrict to your frontend domain, e.g. ["https://neetssplanner.in"]
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

# -------------------------------------------------------------------
# Database Initialization
# -------------------------------------------------------------------
try:
    from app.database import init_db
    @app.on_event("startup")
    def startup_event():
        try:
            init_db()
            print("✅ Database initialized successfully.")
        except Exception as e:
            print(f"⚠️ Database initialization failed: {e}")
except Exception as e:
    print(f"⚠️ Could not import database initialization: {e}")

# -------------------------------------------------------------------
# Routers Registration
# -------------------------------------------------------------------
try:
    # Import core auth utilities (not a router)
    from app import auth  

    # Import API routers
    from app.routers import users, plans, progress
    app.include_router(auth.router, prefix="/api", tags=["Auth"])
    app.include_router(users.router, prefix="/api", tags=["Users"])
    app.include_router(plans.router, prefix="/api", tags=["Plans"])
    app.include_router(progress.router, prefix="/api", tags=["Progress"])

    print("✅ Routers loaded successfully.")
except Exception as e:
    print(f"⚠️ Warning: Routers not loaded → {e}")

# -------------------------------------------------------------------
# Serve Frontend (index.html at root)
# -------------------------------------------------------------------
@app.get("/")
def serve_frontend():
    """Serve frontend UI as the homepage"""
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "NEET SS Planner backend running. Frontend not found."}

# -------------------------------------------------------------------
# Health Check Endpoint
# -------------------------------------------------------------------
@app.get("/health")
def health_check():
    return {"ok": True, "message": "Planner API is healthy and online."}

# -------------------------------------------------------------------
# Local Development Entry Point
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
