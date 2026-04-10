"""
Per-user LMS token cache so nightly jobs can make authenticated calls on
behalf of a user who isn't currently online.

Backends, in priority order:

  1. Redis — if `REDIS_URL` env var is set and `redis` package is available.
     Keys expire automatically after the configured TTL.
  2. In-memory dict — only useful on a single-process uvicorn/gunicorn
     worker. Shared across the process; lost on restart.

The "cryptographic" answer for a multi-worker production deploy is Redis.
The in-memory fallback exists so local dev + single-worker Heroku dynos
still work without extra config.

Public API:

    set_token(lms_user_id, token, ttl_seconds=7*24*3600)
    get_token(lms_user_id) -> Optional[str]
    delete_token(lms_user_id)
    has_token(lms_user_id) -> bool
"""
from __future__ import annotations
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("planner.token_store")

TOKEN_DEFAULT_TTL_S = int(os.getenv("PLANNER_TOKEN_TTL_S", str(7 * 24 * 3600)))

_REDIS_URL = os.getenv("REDIS_URL", "")
_KEY_PREFIX = "cortex:lms_token:"

_redis_client: Any = None
_redis_tried: bool = False


def _get_redis():
    global _redis_client, _redis_tried
    if _redis_client is not None:
        return _redis_client
    if _redis_tried or not _REDIS_URL:
        return None
    _redis_tried = True
    try:
        import redis  # type: ignore
        _redis_client = redis.Redis.from_url(_REDIS_URL, decode_responses=True)
        _redis_client.ping()
        logger.info("[token_store] redis backend active")
        return _redis_client
    except Exception as e:
        logger.warning("[token_store] redis unavailable, falling back to memory: %s", e)
        return None


# { user_id: (expires_at_epoch, token) }
_memory: Dict[str, Tuple[float, str]] = {}


def _purge_memory() -> None:
    now = time.time()
    stale = [k for k, (exp, _) in _memory.items() if exp <= now]
    for k in stale:
        _memory.pop(k, None)


def set_token(lms_user_id: str, token: str, ttl_seconds: int = TOKEN_DEFAULT_TTL_S) -> None:
    if not lms_user_id or not token:
        return
    r = _get_redis()
    if r is not None:
        try:
            r.setex(_KEY_PREFIX + str(lms_user_id), ttl_seconds, token)
            return
        except Exception as e:
            logger.warning("[token_store] redis set failed: %s", e)
    _purge_memory()
    _memory[str(lms_user_id)] = (time.time() + ttl_seconds, token)


def get_token(lms_user_id: str) -> Optional[str]:
    if not lms_user_id:
        return None
    r = _get_redis()
    if r is not None:
        try:
            val = r.get(_KEY_PREFIX + str(lms_user_id))
            return val if val else None
        except Exception as e:
            logger.warning("[token_store] redis get failed: %s", e)
    _purge_memory()
    entry = _memory.get(str(lms_user_id))
    if not entry:
        return None
    expires_at, tok = entry
    if time.time() > expires_at:
        _memory.pop(str(lms_user_id), None)
        return None
    return tok


def delete_token(lms_user_id: str) -> None:
    r = _get_redis()
    if r is not None:
        try:
            r.delete(_KEY_PREFIX + str(lms_user_id))
            return
        except Exception:
            pass
    _memory.pop(str(lms_user_id), None)


def has_token(lms_user_id: str) -> bool:
    return get_token(lms_user_id) is not None
