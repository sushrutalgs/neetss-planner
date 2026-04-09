"""
Smart nudge engine — decides when to ping the user and what to say.

Five nudge families (each is independent so we can A/B-test rollout):

  1. streak_break       — user broke a 7+ day streak yesterday
  2. recall_decay       — >20 due cards piling up
  3. demotivation       — 3 days of declining session length
  4. optimal_window     — user historically studies best at 7-9pm and it's 6:55pm
  5. mock_overdue       — last mock was >14 days ago

Each nudge is a row in the AIRun table (we reuse the existing audit table
instead of adding a new one — `surface = 'nudge'`). The push delivery is
out-of-scope for the planner backend; the LMS notification service polls
unread nudges and pushes via FCM.

Public API:
    compute_nudges_for_user(db, user) -> List[NudgeDict]
    persist_nudges(db, user_id, nudges)
"""
from __future__ import annotations
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude, HAIKU, ClaudeError

logger = logging.getLogger("planner.ai.nudges")


def _streak_break_nudge(daily_activity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    streak = (daily_activity or {}).get("streak", {}) or {}
    if (streak.get("longest", 0) or 0) >= 7 and (streak.get("current", 0) or 0) == 0:
        return {
            "kind": "streak_break",
            "headline": f"You broke a {streak['longest']}-day streak.",
            "body": "Don't let yesterday cost you tomorrow — open the planner now and clock 30 min.",
            "severity": "high",
            "cta": {"label": "Resume now", "deeplink": "/v2#today"},
        }
    return None


def _recall_decay_nudge(due_count: int) -> Optional[Dict[str, Any]]:
    if due_count >= 20:
        return {
            "kind": "recall_decay",
            "headline": f"{due_count} recall cards are due.",
            "body": "Clear them in 10 minutes — recall debt compounds fast.",
            "severity": "medium",
            "cta": {"label": "Review now", "deeplink": "/v2#recall"},
        }
    return None


def _demotivation_nudge(daily_activity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    days = (daily_activity or {}).get("days") or []
    if len(days) < 4:
        return None
    last3 = days[-3:]
    minutes = [d.get("minutes", 0) or 0 for d in last3]
    if minutes[0] > 0 and minutes[2] < minutes[0] * 0.6 and minutes[2] < 60:
        return {
            "kind": "demotivation",
            "headline": "Your sessions are getting shorter — you OK?",
            "body": "Three days of declining minutes. Consider a 25-min Pomodoro on something easy.",
            "severity": "medium",
            "cta": {"label": "Start a Pomodoro", "deeplink": "/v2#timer"},
        }
    return None


def _optimal_window_nudge(daily_activity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Heuristic — would be smarter with hour-of-day data; this version
    just notes the user's modal session window from the daily-activity feed."""
    optimal = (daily_activity or {}).get("optimal_window")
    if not optimal:
        return None
    now_hour = datetime.now().hour
    start = int(optimal.get("start_hour", 19))
    if now_hour == start - 1:
        return {
            "kind": "optimal_window",
            "headline": f"Your golden hour starts at {start}:00.",
            "body": "Historically your best session of the day. Lock the next 90 min.",
            "severity": "low",
            "cta": {"label": "Open today", "deeplink": "/v2#today"},
        }
    return None


def _mock_overdue_nudge(mock_history: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mocks = (mock_history or {}).get("mocks") or []
    if not mocks:
        return {
            "kind": "mock_overdue",
            "headline": "You haven't sat a mock yet.",
            "body": "Even one mock unlocks the rank prediction. Block 3 hours this weekend.",
            "severity": "medium",
            "cta": {"label": "Browse mocks", "deeplink": "/v2#mocks"},
        }
    last = mocks[0]
    last_date_str = last.get("date") or last.get("attempted_at")
    if not last_date_str:
        return None
    try:
        last_date = datetime.fromisoformat(last_date_str.replace("Z", "")).date()
    except Exception:
        return None
    if (date.today() - last_date).days >= 14:
        return {
            "kind": "mock_overdue",
            "headline": f"It's been {(date.today() - last_date).days} days since your last mock.",
            "body": "Mock cadence is the strongest predictor of rank movement. Sit one this week.",
            "severity": "medium",
            "cta": {"label": "Browse mocks", "deeplink": "/v2#mocks"},
        }
    return None


def compute_nudges_for_user(db, user) -> List[Dict[str, Any]]:
    """Run all five nudge detectors against the user's current state."""
    if not user or not user.lms_user_id:
        return []
    try:
        from app.lms_client import (
            service_token_for, get_user_daily_activity, get_user_mock_history, LmsError,
        )
        token = service_token_for(user.lms_user_id)
        daily = get_user_daily_activity(token, days=14) or {}
        mocks = get_user_mock_history(token, limit=10) or {}
    except Exception as e:
        logger.warning("[nudges] LMS fetch failed for user %s: %s", user.id, e)
        return []

    # Recall due count from local DB.
    due_count = 0
    try:
        from app.models import RecallCard
        due_count = (
            db.query(RecallCard)
            .filter(RecallCard.user_id == user.id, RecallCard.next_review_date <= date.today())
            .count()
        )
    except Exception:
        pass

    detectors = [
        _streak_break_nudge(daily),
        _recall_decay_nudge(due_count),
        _demotivation_nudge(daily),
        _optimal_window_nudge(daily),
        _mock_overdue_nudge(mocks),
    ]
    return [n for n in detectors if n]


def llm_polish_nudge(nudge: Dict[str, Any], user_name: str = "Doctor") -> Dict[str, Any]:
    """
    Optional Haiku pass to humanise the headline/body. Falls back silently
    on Claude failure (the deterministic copy is already shippable).
    """
    payload = {"name": user_name, "nudge": nudge}
    system = (
        "You rewrite push notifications for a NEET SS surgical exam coaching app. "
        "Tone: warm, direct, never preachy. Headline ≤ 8 words. Body ≤ 22 words. "
        "Output JSON only."
    )
    prompt = (
        f"Rewrite this nudge so it feels personal:\n{json.dumps(payload)}\n\n"
        "Return JSON: { \"headline\": \"...\", \"body\": \"...\" }. JSON only."
    )
    try:
        result = call_claude(prompt=prompt, system=system, model=HAIKU, max_tokens=300, temperature=0.8)
        text = (result.get("text") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        out = json.loads(text)
        nudge["headline"] = out.get("headline", nudge["headline"])
        nudge["body"] = out.get("body", nudge["body"])
    except (ClaudeError, ValueError, json.JSONDecodeError):
        pass
    return nudge


def persist_nudges(db, user_id: int, nudges: List[Dict[str, Any]]) -> None:
    """Write each nudge as an AIRun row so the LMS notification poller can pick it up."""
    if not nudges:
        return
    try:
        from app.models import AIRun
        for n in nudges:
            db.add(AIRun(
                user_id=user_id,
                surface="nudge",
                model="local",
                input_tokens=0,
                output_tokens=0,
                output_md=json.dumps(n)[:65000],
                created_at=datetime.utcnow(),
            ))
        db.commit()
    except Exception as e:
        logger.warning("[nudges] persist failed for user %s: %s", user_id, e)
