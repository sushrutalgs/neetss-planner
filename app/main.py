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
    allow_origins=["*"],  # you can restrict to your frontend domain later
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
# Routers
# -------------------------------------------------------------------
try:
    from app.routers import auth, planner, plans
    app.include_router(auth.router, prefix="/api", tags=["Authentication"])
    app.include_router(planner.router, prefix="/api", tags=["Planner"])
    app.include_router(plans.router, prefix="/api", tags=["Plans"])
except ImportError as e:
    print("⚠️ Warning: Routers not yet available — continuing without them.", e)

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
# Local Development (optional)
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
