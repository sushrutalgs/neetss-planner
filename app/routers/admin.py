from __future__ import annotations
import csv
from io import StringIO
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import models, database
from app.schemas import AdminResetPassword
from app.auth import require_admin, get_password_hash

router = APIRouter(prefix="/api/admin", tags=["Admin"])


@router.get("/stats")
def get_admin_stats(db: Session = Depends(database.get_db), admin=Depends(require_admin)):
    total_users = db.query(models.User).count()
    total_plans = db.query(models.Plan).count()
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    plans = db.query(models.Plan).order_by(models.Plan.created_at.desc()).all()

    # Pending password reset codes
    pending_resets = (
        db.query(models.PasswordReset)
        .filter(
            models.PasswordReset.used == False,
            models.PasswordReset.expires_at > datetime.utcnow(),
        )
        .order_by(models.PasswordReset.created_at.desc())
        .all()
    )

    return {
        "total_users": total_users,
        "total_plans": total_plans,
        "users": [
            {
                "id": u.id, "name": u.name, "email": u.email,
                "goal": u.goal,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
        "plans": [
            {
                "id": p.id, "user_id": p.user_id, "name": p.name,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in plans
        ],
        "pending_resets": [
            {
                "id": r.id, "email": r.email, "code": r.reset_code,
                "expires_at": r.expires_at.isoformat(),
                "created_at": r.created_at.isoformat(),
            }
            for r in pending_resets
        ],
    }


@router.post("/reset-user-password")
def admin_reset_password(
    payload: AdminResetPassword,
    db: Session = Depends(database.get_db),
    admin=Depends(require_admin),
):
    """Admin can directly reset any user's password."""
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = get_password_hash(payload.new_password)
    db.add(user); db.commit()
    return {"ok": True, "message": f"Password reset for {user.email}"}


@router.get("/export")
def export_csv(db: Session = Depends(database.get_db), admin=Depends(require_admin)):
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["--- USERS ---"])
    writer.writerow(["ID", "Name", "Email", "Goal", "Joined"])
    for u in db.query(models.User).order_by(models.User.created_at.desc()).all():
        writer.writerow([u.id, u.name, u.email, u.goal or "", u.created_at.isoformat() if u.created_at else ""])
    writer.writerow([])
    writer.writerow(["--- PLANS ---"])
    writer.writerow(["ID", "User ID", "User Email", "Plan Name", "Created"])
    for p, email in db.query(models.Plan, models.User.email).join(models.User).order_by(models.Plan.created_at.desc()).all():
        writer.writerow([p.id, p.user_id, email, p.name or "", p.created_at.isoformat() if p.created_at else ""])
    return Response(content=buf.getvalue().encode("utf-8"), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="cortex_export.csv"'})
