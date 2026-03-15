from __future__ import annotations
import csv
from io import StringIO
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import models, database
from app.auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ============================================================
#                     ADMIN STATS
# ============================================================

@router.get("/stats")
def get_admin_stats(
    db: Session = Depends(database.get_db),
    admin=Depends(require_admin),
):
    """Get platform overview stats (admin only)."""
    total_users = db.query(models.User).count()
    total_plans = db.query(models.Plan).count()

    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    plans = db.query(models.Plan).order_by(models.Plan.created_at.desc()).all()

    return {
        "total_users": total_users,
        "total_plans": total_plans,
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "goal": u.goal,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
        "plans": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "name": p.name,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in plans
        ],
    }


# ============================================================
#                     ADMIN CSV EXPORT
# ============================================================

@router.get("/export")
def export_csv(
    db: Session = Depends(database.get_db),
    admin=Depends(require_admin),
):
    """Download all users and plans as a CSV file (admin only)."""
    buf = StringIO()
    writer = csv.writer(buf)

    # Users sheet
    writer.writerow(["--- USERS ---"])
    writer.writerow(["ID", "Name", "Email", "Goal", "Joined"])
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    for u in users:
        writer.writerow([
            u.id,
            u.name,
            u.email,
            u.goal or "",
            u.created_at.isoformat() if u.created_at else "",
        ])

    writer.writerow([])

    # Plans sheet
    writer.writerow(["--- PLANS ---"])
    writer.writerow(["ID", "User ID", "User Email", "Plan Name", "Created"])
    plans = (
        db.query(models.Plan, models.User.email)
        .join(models.User, models.Plan.user_id == models.User.id)
        .order_by(models.Plan.created_at.desc())
        .all()
    )
    for p, email in plans:
        writer.writerow([
            p.id,
            p.user_id,
            email,
            p.name or "",
            p.created_at.isoformat() if p.created_at else "",
        ])

    csv_content = buf.getvalue().encode("utf-8")
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cortex_planner_export.csv"'},
    )
