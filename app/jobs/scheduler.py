"""
APScheduler bootstrapper. Mounted from app/main.py lifespan.

Runs three nightly jobs in IST:

  03:00  nightly_replan       — rebuilds every active plan from fresh LMS signal
  03:30  nightly_nudges       — checks streak/decay/demotivation, queues push copy
  04:00  nightly_diagnostics  — refreshes question-level diagnostics for new attempts
"""
from __future__ import annotations
import logging
import os
from datetime import datetime

logger = logging.getLogger("planner.jobs")

_scheduler = None


def start_scheduler() -> None:
    """Boots APScheduler in the FastAPI lifespan. Idempotent."""
    global _scheduler
    if _scheduler is not None:
        return
    if os.getenv("PLANNER_DISABLE_JOBS", "").lower() in ("1", "true", "yes"):
        logger.info("[jobs] PLANNER_DISABLE_JOBS set — skipping scheduler boot")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[jobs] apscheduler not installed — nightly jobs disabled")
        return

    sched = BackgroundScheduler(timezone="Asia/Kolkata")

    sched.add_job(
        _safe(_run_nightly_replan),
        CronTrigger(hour=3, minute=0),
        id="nightly_replan",
        replace_existing=True,
    )
    sched.add_job(
        _safe(_run_nightly_nudges),
        CronTrigger(hour=3, minute=30),
        id="nightly_nudges",
        replace_existing=True,
    )
    sched.add_job(
        _safe(_run_nightly_diagnostics),
        CronTrigger(hour=4, minute=0),
        id="nightly_diagnostics",
        replace_existing=True,
    )

    sched.start()
    _scheduler = sched
    logger.info("[jobs] APScheduler started — 3 nightly jobs registered (IST)")


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None


def _safe(fn):
    """Wrap a job so an exception inside it can never crash the scheduler thread."""
    def wrapper(*args, **kwargs):
        started = datetime.utcnow()
        try:
            logger.info("[jobs] %s starting", fn.__name__)
            fn(*args, **kwargs)
            elapsed = (datetime.utcnow() - started).total_seconds()
            logger.info("[jobs] %s finished in %.1fs", fn.__name__, elapsed)
        except Exception as e:
            logger.exception("[jobs] %s crashed: %s", fn.__name__, e)
    return wrapper


# ─────────────────────────── job bodies ───────────────────────────


def _run_nightly_replan() -> None:
    """
    Walk every (user, active plan) pair and rebuild the schedule from the
    freshest LMS signal. The job is idempotent — safe to re-run.
    """
    from app.database import SessionLocal
    from app.models import Plan, User
    from app.lms_client import (
        get_syllabus_bundle,
        get_user_signal,
        LmsError,
    )
    from app.content_scheduler import SchedulerConfig, build_schedule_from_signal
    from app.routers.planner_v2 import _persist_mastery_vector, _user_multipliers, _load_due_recall
    from datetime import date as _date, datetime as _dt
    from sqlalchemy.orm.attributes import flag_modified

    db = SessionLocal()
    try:
        active = (
            db.query(Plan)
            .filter(Plan.is_archived == False)  # noqa: E712
            .filter(Plan.end_date >= _date.today())
            .all()
        )
        logger.info("[nightly_replan] %d active plans to refresh", len(active))
        for plan in active:
            user = db.query(User).filter(User.id == plan.user_id).first()
            if not user or not user.lms_user_id:
                continue
            # Service-token mode: the LMS exposes a planner-service token endpoint
            # the planner caches per user. Fall back to skipping if absent.
            from app.lms_client import service_token_for
            try:
                token = service_token_for(user.lms_user_id)
            except Exception as e:
                logger.warning("[nightly_replan] no token for user %s: %s", user.id, e)
                continue

            try:
                bundle = get_syllabus_bundle(token) or {}
            except LmsError:
                bundle = {}
            try:
                user_signal = get_user_signal(token) or {}
            except LmsError:
                user_signal = {}

            cfg_in = plan.config_json or {}
            cfg = SchedulerConfig(
                start_date=max(_date.today(), plan.start_date or _date.today()),
                end_date=plan.end_date,
                hours_per_day=float(cfg_in.get("hours_per_day", 4.0)),
                rest_days_per_week=int(cfg_in.get("rest_days_per_week", 1)),
                custom_rest_dates=[],
                use_lms_content=bool(plan.use_lms_content and bundle.get("categories")),
                daily_minutes=plan.daily_minutes or cfg_in.get("daily_minutes"),
                mocks_count=cfg_in.get("mocks_count"),
                min_per_mcq=float(cfg_in.get("min_per_mcq", 1.5)),
                revision_rounds=int(cfg_in.get("revision_rounds", 1)),
                focus_topic_ids=cfg_in.get("focus_topic_ids", []) or [],
            )
            due = _load_due_recall(db, user.id)
            try:
                days, mastery_vector = build_schedule_from_signal(
                    bundle=bundle,
                    cfg=cfg,
                    user_signal=user_signal,
                    fsrs_cards_by_topic=None,
                    due_recall_cards=due,
                    user_multipliers=_user_multipliers(user),
                )
            except Exception as e:
                logger.warning("[nightly_replan] schedule failed for user %s: %s", user.id, e)
                continue

            try:
                _persist_mastery_vector(db, user.id, mastery_vector)
            except Exception as e:
                logger.warning("[nightly_replan] persist failed for user %s: %s", user.id, e)

            data = plan.data_json or {}
            data["days"] = days
            plan.data_json = data
            flag_modified(plan, "data_json")
            plan.last_replan_at = _dt.utcnow()
            plan.last_rebuild_at = _dt.utcnow()
            plan.needs_rebuild = False
            db.commit()
    finally:
        db.close()


def _run_nightly_nudges() -> None:
    """Streak-break, recall-decay, demotivation — write a NudgeQueue row per user."""
    from app.database import SessionLocal
    from app.models import User
    try:
        from app.ai.nudges import compute_nudges_for_user, persist_nudges
    except Exception as e:
        logger.warning("[nightly_nudges] nudges module not available: %s", e)
        return

    db = SessionLocal()
    try:
        users = db.query(User).filter(User.lms_user_id.isnot(None)).all()
        logger.info("[nightly_nudges] %d users to evaluate", len(users))
        for u in users:
            try:
                nudges = compute_nudges_for_user(db, u)
                if nudges:
                    persist_nudges(db, u.id, nudges)
            except Exception as e:
                logger.warning("[nightly_nudges] user %s failed: %s", u.id, e)
    finally:
        db.close()


def _run_nightly_diagnostics() -> None:
    """Re-run question-level diagnostics for users who attempted MCQs in the last 24h."""
    from app.database import SessionLocal
    from app.models import User
    try:
        from app.ai.diagnostics import refresh_user_diagnostics
    except Exception as e:
        logger.warning("[nightly_diagnostics] module not available: %s", e)
        return

    db = SessionLocal()
    try:
        users = db.query(User).filter(User.lms_user_id.isnot(None)).all()
        for u in users:
            try:
                refresh_user_diagnostics(db, u)
            except Exception as e:
                logger.warning("[nightly_diagnostics] user %s failed: %s", u.id, e)
    finally:
        db.close()
