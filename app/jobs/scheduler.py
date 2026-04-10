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
    freshest LMS signals. The job is idempotent — safe to re-run.

    Token model: uses the last-seen LMS token cached by auth_lms.get_lms_user
    via token_store. Users who haven't hit the API in TOKEN_DEFAULT_TTL_S
    (7 days) are skipped with reason=no_token. This replaces the broken
    `service_token_for()` synthetic token scheme.
    """
    from app.database import SessionLocal
    from app.models import Plan, User
    from app.lms_client import fetch_all_signals
    from app.token_store import get_token
    from app.content_scheduler import SchedulerConfig, build_schedule_from_signal
    from app.ai.mastery import build_vector as build_mastery_vector
    from app.ai.plan_shaper import shape_plan
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
        skipped_no_token = 0
        rebuilt = 0
        logger.info("[nightly_replan] %d active plans to refresh", len(active))
        for plan in active:
            user = db.query(User).filter(User.id == plan.user_id).first()
            if not user or not user.lms_user_id:
                continue
            token = get_token(user.lms_user_id)
            if not token:
                skipped_no_token += 1
                logger.info(
                    "[nightly_replan] skip user=%s plan=%s reason=no_token",
                    user.id, plan.id,
                )
                continue

            try:
                signals = fetch_all_signals(token) or {}
            except Exception as e:
                logger.warning("[nightly_replan] fetch signals failed user=%s: %s", user.id, e)
                continue

            bundle = signals.get("bundle") or {}
            user_signal = signals.get("signal") or {}
            mcq_history = signals.get("mcq_history") or {}
            content_progress = signals.get("content_progress") or {}
            mock_history = signals.get("mock_history") or {}
            daily_activity = signals.get("daily_activity") or {}

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

            flat_topics = []
            for cat in (bundle.get("categories") or []):
                for sub in (cat.get("subcategories") or []):
                    for t in (sub.get("topics") or []):
                        flat_topics.append({**t, "_category_name": cat.get("name")})

            mastery_vector = build_mastery_vector(
                lms_signal=user_signal,
                mcq_history=mcq_history,
                content_progress=content_progress,
                mock_history=mock_history,
                daily_activity=daily_activity,
                fsrs_cards_by_topic=None,
                bundle_topics=flat_topics,
            )

            plan_shape = None
            try:
                if cfg.use_lms_content:
                    plan_shape = shape_plan(
                        mastery_vector=mastery_vector,
                        bundle=bundle,
                        start_date=cfg.start_date,
                        end_date=cfg.end_date,
                        hours_per_day=cfg.hours_per_day,
                        subscription_status=user.subscription_status or "active",
                        daily_activity=daily_activity,
                    )
            except Exception as e:
                logger.warning("[nightly_replan] shaper failed user=%s: %s", user.id, e)
                plan_shape = None

            due = _load_due_recall(db, user.id)
            try:
                days, mastery_vector = build_schedule_from_signal(
                    bundle=bundle,
                    cfg=cfg,
                    user_signal=user_signal,
                    mcq_history=mcq_history,
                    content_progress=content_progress,
                    mock_history=mock_history,
                    daily_activity=daily_activity,
                    fsrs_cards_by_topic=None,
                    due_recall_cards=due,
                    user_multipliers=_user_multipliers(user),
                    plan_shape=plan_shape,
                    mastery_vector_override=mastery_vector,
                )
            except Exception as e:
                logger.warning("[nightly_replan] schedule failed user=%s: %s", user.id, e)
                continue

            try:
                _persist_mastery_vector(db, user.id, mastery_vector)
            except Exception as e:
                logger.warning("[nightly_replan] persist failed user=%s: %s", user.id, e)

            data = plan.data_json or {}
            data["days"] = days
            if plan_shape:
                data["plan_shape"] = plan_shape
            plan.data_json = data
            flag_modified(plan, "data_json")
            plan.last_replan_at = _dt.utcnow()
            plan.last_rebuild_at = _dt.utcnow()
            plan.needs_rebuild = False
            db.commit()
            rebuilt += 1

        logger.info(
            "[nightly_replan] done: rebuilt=%d skipped_no_token=%d",
            rebuilt, skipped_no_token,
        )
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
