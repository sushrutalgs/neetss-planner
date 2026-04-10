"""
Cortex Surgery Planner ↔ Sushruta LMS API client.

Single place that talks to the LMS Node API. Other planner modules import
from here so the contract surface is small and well-known.

Endpoints consumed:
  GET /api/planner/syllabus-bundle   — full content tree for the user
  GET /api/planner/user-state        — subscription/state probe
  GET /api/planner/mcq-batch         — server-picked MCQs for a topic

The planner forwards the user's LMS JWT (received in the Authorization
header) directly to the LMS — no separate identity. Federation is via
the shared JWT secret, so any token the LMS minted is also accepted by
the planner via verify_lms_token().
"""
from __future__ import annotations
import os
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("planner.lms_client")

LMS_BASE = os.getenv("LMS_BASE_URL", "").rstrip("/")
LMS_TIMEOUT_S = float(os.getenv("LMS_TIMEOUT_S", "10"))
PLANNER_SERVICE_TOKEN = os.getenv("PLANNER_SERVICE_TOKEN", "")  # shared secret for nightly jobs


def service_token_for(lms_user_id: str) -> str:
    """
    Returns a synthetic service token the LMS planner middleware accepts for
    server-to-server jobs (nightly replan, nudges). The LMS validates the
    shared PLANNER_SERVICE_TOKEN secret and trusts the X-LMS-User-Id header.
    """
    if not PLANNER_SERVICE_TOKEN:
        raise LmsError("PLANNER_SERVICE_TOKEN not configured — nightly jobs disabled")
    # The LMS planner.route.js handler reads both auth header and X-LMS-User-Id
    # when the auth value matches the shared secret prefix.
    return f"service:{PLANNER_SERVICE_TOKEN}:{lms_user_id}"


class LmsError(Exception):
    """Raised on LMS API failures."""


def _client() -> httpx.Client:
    if not LMS_BASE:
        raise LmsError("LMS_BASE_URL not configured")
    return httpx.Client(base_url=LMS_BASE, timeout=LMS_TIMEOUT_S)


def _headers(token: str) -> Dict[str, str]:
    # The LMS middleware accepts both raw and "Bearer <token>" — we send raw
    # to match the way the rest of the LMS clients (admin SPA, Flutter app)
    # already use it.
    return {"authorization": token}


def get_syllabus_bundle(token: str) -> Dict[str, Any]:
    """Fetch the full hierarchical content tree the authed user can access."""
    try:
        with _client() as c:
            r = c.get("/api/planner/syllabus-bundle", headers=_headers(token))
        if r.status_code != 200:
            raise LmsError(f"syllabus-bundle http {r.status_code}: {r.text[:300]}")
        body = r.json()
        # The LMS wraps every response in { status, data } via success() helper.
        return body.get("data", body)
    except httpx.HTTPError as e:
        raise LmsError(f"syllabus-bundle network: {e}") from e


def get_user_state(token: str) -> Dict[str, Any]:
    """Lightweight subscription probe — used by the polling reconciler."""
    try:
        with _client() as c:
            r = c.get("/api/planner/user-state", headers=_headers(token))
        if r.status_code != 200:
            raise LmsError(f"user-state http {r.status_code}: {r.text[:300]}")
        body = r.json()
        return body.get("data", body)
    except httpx.HTTPError as e:
        raise LmsError(f"user-state network: {e}") from e


def get_mcq_batch(
    token: str,
    topic_id: str,
    count: int = 30,
    exclude_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Server-picked MCQ batch for a topic."""
    params: Dict[str, Any] = {"topic_id": topic_id, "count": count}
    if exclude_ids:
        params["exclude"] = ",".join(exclude_ids)
    try:
        with _client() as c:
            r = c.get("/api/planner/mcq-batch", headers=_headers(token), params=params)
        if r.status_code != 200:
            raise LmsError(f"mcq-batch http {r.status_code}: {r.text[:300]}")
        body = r.json()
        return body.get("data", body)
    except httpx.HTTPError as e:
        raise LmsError(f"mcq-batch network: {e}") from e


# ───────────────────────── ML signal endpoints ─────────────────────────
#
# Five new federated endpoints exposed by the LMS at api-ruchir-optimization
# in src/routes/planner.route.js. These power the planner's mastery model,
# adaptive scheduler, and "what to improve" recommendations. All return
# { status, data } and we unwrap to the data.

def _get_json(token: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Shared GET wrapper that unwraps {status, data} and surfaces LmsError."""
    try:
        with _client() as c:
            r = c.get(path, headers=_headers(token), params=params or None)
        if r.status_code != 200:
            raise LmsError(f"{path} http {r.status_code}: {r.text[:300]}")
        body = r.json()
        return body.get("data", body)
    except httpx.HTTPError as e:
        raise LmsError(f"{path} network: {e}") from e


def get_user_mcq_history(token: str, since_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Per-topic MCQ accuracy + attempts + last-attempted, with a server-side
    mastery prior. The Analytics tab and the planner's mastery reconciler
    both consume this.

    Returns:
      { since, generated_at, by_topic: [...], totals: {...} }
    """
    params: Dict[str, Any] = {}
    if since_iso:
        params["since"] = since_iso
    return _get_json(token, "/api/planner/user-mcq-history", params)


def get_user_content_progress(token: str, since_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Video watches + PDF page progress, both flat lists and a per-topic
    rollup. The scheduler reads `by_topic` to know which topics already
    have content engagement vs which are cold.

    Returns:
      { since, generated_at, videos, notes, by_topic, totals }
    """
    params: Dict[str, Any] = {}
    if since_iso:
        params["since"] = since_iso
    return _get_json(token, "/api/planner/user-content-progress", params)


def get_user_mock_history(token: str, limit: int = 30) -> Dict[str, Any]:
    """
    Flat mock attempt history with score, accuracy, predicted rank, and a
    5-attempt trend. Drives the Analytics tab's Predicted Performance card
    and the scheduler's mock cadence (denser pre-end-date).

    Returns:
      { generated_at, mocks, latest_predicted_rank, trend_last_5, totals }
    """
    return _get_json(token, "/api/planner/user-mock-history", {"limit": limit})


def get_user_daily_activity(token: str, days: int = 90) -> Dict[str, Any]:
    """
    Day-bucketed activity for the streak chip + Progress heatmap + the
    scheduler's daily_minutes default. Unions MCQs/videos/notes/mocks.

    Returns:
      { generated_at, days[], streak: {current, longest},
        avg_minutes_last_14d, total_minutes_window, window_days }
    """
    return _get_json(token, "/api/planner/user-daily-activity", {"days": days})


def get_user_signal(token: str) -> Dict[str, Any]:
    """
    The one-shot composite. Single LMS round-trip that returns identity +
    subscription + computed mastery vector + ranked weakest/strongest
    topics + 14d streak + latest predicted rank.

    The planner calls this at plan generation, at every nightly replan,
    and any time the user opens the Dashboard cold. Cached server-side
    for ~3 minutes.

    Returns:
      {
        user: { user_id, name, email, subscription_status, ... },
        computed: {
          avg_daily_minutes_14d, active_days_14d, streak_current,
          mock_attempts_total, latest_predicted_rank,
          mastery_hint: [ { topic_id, topic_name, attempted, correct,
                            accuracy, mastery, confidence, gap,
                            last_seen_days_ago } ],
          weakest_topics: [...top 5 with gap > 0.3, attempted >= 5],
          strongest_topics: [...top 5 with mastery >= 0.7],
        }
      }
    """
    return _get_json(token, "/api/planner/user-signal")


def get_cohort_stats(token: str, exam_type: str = "NEET_SS") -> Dict[str, Any]:
    """
    Aggregate cohort distributions for peer benchmarking. Pre-aggregated
    server-side; never returns user identities.

    Returns:
      { exam_type, n_users, distributions: { mastery_avg: [...], ... },
        topic_means: { topic_id: float }, topic_trends: [...] }
    """
    return _get_json(token, "/api/planner/cohort-stats", {"exam_type": exam_type})


def _normalize_phone(phone: str) -> str:
    """Strip spaces, dashes, parens and a leading +91/91/0 — LMS expects
    the raw 10-digit local number, then hardcodes the 91 country prefix
    in the MSG91 request itself (see wati.controller.js)."""
    if not phone:
        return ""
    p = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(p) > 10 and p.startswith("91"):
        p = p[2:]
    if len(p) == 11 and p.startswith("0"):
        p = p[1:]
    return p


def send_otp_sms(phone: str) -> Dict[str, Any]:
    """Step 1 of mobile OTP login — asks the LMS to SMS a 4-digit OTP via MSG91.
    Mirrors the same endpoint the Sushruta mobile/web app already calls."""
    phone = _normalize_phone(phone)
    if len(phone) != 10:
        raise LmsError("phone must be a 10-digit mobile number")
    try:
        with _client() as c:
            r = c.post("/api/sendOtpViaSms", json={"phone": phone})
        if r.status_code != 200:
            raise LmsError(f"sendOtpViaSms http {r.status_code}: {r.text[:200]}")
        return r.json()
    except httpx.HTTPError as e:
        raise LmsError(f"sendOtpViaSms network: {e}") from e


def login_via_sms(
    phone: str,
    otp: str,
    device_type: str = "desktop",
    device_id: str = "cortex-web",
    device_name: str = "Cortex Web",
    device_unique_id: str = "cortex-web",
) -> Dict[str, Any]:
    """Step 2 of mobile OTP login — verifies SMS OTP and returns an LMS
    session token. Mirrors the endpoint the Sushruta app already calls."""
    body = {
        "phone": _normalize_phone(phone),
        "userOTP": str(otp),
        "deviceType": device_type,
        "deviceId": device_id,
        "deviceName": device_name,
        "deviceUniqueId": device_unique_id,
    }
    try:
        with _client() as c:
            r = c.post("/api/loginViaSms", json=body)
        if r.status_code != 200:
            raise LmsError(f"loginViaSms http {r.status_code}: {r.text[:200]}")
        return r.json()
    except httpx.HTTPError as e:
        raise LmsError(f"loginViaSms network: {e}") from e


def fetch_all_signals(
    token: str,
    *,
    mcq_since_iso: Optional[str] = None,
    content_since_iso: Optional[str] = None,
    mock_limit: int = 30,
    activity_days: int = 90,
) -> Dict[str, Any]:
    """
    Pull every student-activity signal the planner cares about in parallel.

    Returns a dict with the raw LMS payloads so callers can pipe them
    straight into ai.mastery.build_vector:

        {
          "bundle":          {...},                # syllabus tree
          "signal":          {...},                # user-signal composite
          "mcq_history":     {...},
          "content_progress":{...},
          "mock_history":    {...},
          "daily_activity":  {...},
          "errors":          {"mcq_history": "...", ...}
        }

    Network errors on any individual endpoint are captured in `errors` and
    the corresponding slot is set to an empty dict. The caller decides
    whether to proceed (always does — we prefer degraded plans to 500s).
    """
    from concurrent.futures import ThreadPoolExecutor

    results: Dict[str, Any] = {
        "bundle": {},
        "signal": {},
        "mcq_history": {},
        "content_progress": {},
        "mock_history": {},
        "daily_activity": {},
        "errors": {},
    }

    jobs: Dict[str, Any] = {
        "bundle": lambda: get_syllabus_bundle(token),
        "signal": lambda: get_user_signal(token),
        "mcq_history": lambda: get_user_mcq_history(token, since_iso=mcq_since_iso),
        "content_progress": lambda: get_user_content_progress(token, since_iso=content_since_iso),
        "mock_history": lambda: get_user_mock_history(token, limit=mock_limit),
        "daily_activity": lambda: get_user_daily_activity(token, days=activity_days),
    }

    with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, fut in futures.items():
            try:
                val = fut.result()
                results[name] = val or {}
            except LmsError as e:
                results["errors"][name] = str(e)
                logger.warning("[fetch_all_signals] %s failed: %s", name, e)
            except Exception as e:  # pragma: no cover
                results["errors"][name] = str(e)
                logger.warning("[fetch_all_signals] %s crashed: %s", name, e)

    return results


def emit_planner_event(token: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Send a planner-side event back to the LMS event bus (e.g. plan_generated)."""
    try:
        with _client() as c:
            r = c.post(
                "/api/planner/events",
                headers=_headers(token),
                json={"event_type": event_type, "payload": payload},
            )
        if r.status_code not in (200, 201, 202):
            raise LmsError(f"events http {r.status_code}: {r.text[:200]}")
        return r.json().get("data", {})
    except httpx.HTTPError as e:
        raise LmsError(f"events network: {e}") from e
