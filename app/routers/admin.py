from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app import models, database
from app.auth import get_current_user  # adjust path if different

router = APIRouter(prefix="/api/admin", tags=["Admin"])

@router.get("/admin/stats")
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
                "goal": u.goal,
                "created_at": u.created_at,
            }
            for u in users
        ],
        "plans": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "name": p.name,
                "created_at": p.created_at,
            }
            for p in plans
        ],
    }

