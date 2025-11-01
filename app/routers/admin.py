from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app import models, database
from app.auth import get_current_user  # adjust path if needed

router = APIRouter(prefix="/api/admin", tags=["Admin"])

@router.get("/stats")  # 👈 only '/stats' (not '/admin/stats')
def get_admin_stats(db: Session = Depends(database.get_db), user=Depends(get_current_user)):
    # Restrict to admin (you)
    if user.email != "sushrutalgs@gmail.com":
        raise HTTPException(status_code=403, detail="Access denied")

    total_users = db.query(models.User).count()
    total_plans = db.query(models.Plan).count()

    users = db.query(models.User).all()
    plans = db.query(models.Plan).all()

    return {
        "total_users": total_users,
        "total_plans": total_plans,
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "goal": getattr(u, "goal", None),
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ],
        "plans": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "name": getattr(p, "name", None),
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in plans
        ],
    }
