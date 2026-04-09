"""
Claude API client for the Cortex Surgery Planner.

Python port of the LMS-side `aiUtils.business.js` callClaude wrapper, with the
same conventions: 32K max_tokens default, stop_reason capture, exponential
backoff on 429/5xx, and structured return shape that includes token usage.

Models used across the planner:
  - claude-opus-4-6     — hardest reasoning (mock analysis, weak-topic deep dive)
  - claude-sonnet-4-6   — bulk generation (weekly coach, plan rationale, RAG chat)
  - claude-haiku-4-5    — high-volume cheap calls (daily briefings, push copy)

Usage:
    from app.ai.claude import call_claude

    result = call_claude(
        prompt="Summarise this week's MCQ performance...",
        system="You are a NEET SS surgical study coach.",
        model="claude-sonnet-4-6",
        max_tokens=8000,
    )
    print(result["text"])           # generated text
    print(result["stop_reason"])    # 'end_turn' | 'max_tokens' | 'tool_use' | ...
    print(result["input_tokens"], result["output_tokens"])
"""
from __future__ import annotations
import os
import time
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("planner.ai.claude")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_BASE = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_VERSION = "2023-06-01"

# Model id constants — single source of truth so callers never hard-code strings.
OPUS = "claude-opus-4-6"
SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5"

DEFAULT_MAX_TOKENS = 32000
DEFAULT_TIMEOUT_S = 120.0


class ClaudeError(Exception):
    """Raised when Claude returns a non-recoverable error."""


def call_claude(
    prompt: str,
    system: Optional[str] = None,
    model: str = SONNET,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 1.0,
    stop_sequences: Optional[List[str]] = None,
    extra_messages: Optional[List[Dict[str, Any]]] = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    retries: int = 4,
) -> Dict[str, Any]:
    """
    Synchronous Claude call. Returns:
        {
          "text":           <assistant text>,
          "tokens":         <input + output>,
          "input_tokens":   int,
          "output_tokens":  int,
          "stop_reason":    str,
          "model":          str,
          "raw":            <full response dict for debugging>,
        }

    Retries on 429 and 5xx with exponential backoff. Logs a warning when
    stop_reason == 'max_tokens' so callers can spot truncation in logs the
    same way the LMS-side wrapper does.
    """
    if not ANTHROPIC_API_KEY:
        raise ClaudeError("ANTHROPIC_API_KEY is not configured")

    messages: List[Dict[str, Any]] = []
    if extra_messages:
        messages.extend(extra_messages)
    messages.append({"role": "user", "content": prompt})

    body: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        body["system"] = system
    if stop_sequences:
        body["stop_sequences"] = stop_sequences

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    url = f"{ANTHROPIC_BASE}/v1/messages"
    backoff = 1.0
    last_err: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.post(url, headers=headers, json=body)
            if resp.status_code == 200:
                data = resp.json()
                text = ""
                for block in data.get("content", []) or []:
                    if block.get("type") == "text":
                        text += block.get("text", "")
                usage = data.get("usage", {}) or {}
                input_tokens = int(usage.get("input_tokens", 0))
                output_tokens = int(usage.get("output_tokens", 0))
                stop_reason = data.get("stop_reason", "") or ""
                if stop_reason == "max_tokens":
                    logger.warning(
                        "[claude] response TRUNCATED at max_tokens (%d tok, output=%d) model=%s",
                        max_tokens, output_tokens, model,
                    )
                return {
                    "text": text,
                    "tokens": input_tokens + output_tokens,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "stop_reason": stop_reason,
                    "model": data.get("model", model),
                    "raw": data,
                }

            # Recoverable errors → backoff
            if resp.status_code in (429, 500, 502, 503, 504, 529):
                last_err = ClaudeError(f"http {resp.status_code}: {resp.text[:300]}")
                if attempt < retries:
                    sleep_for = backoff
                    # honor Retry-After if provided
                    ra = resp.headers.get("retry-after")
                    if ra:
                        try:
                            sleep_for = max(sleep_for, float(ra))
                        except ValueError:
                            pass
                    logger.warning(
                        "[claude] %s, retrying in %.1fs (attempt %d/%d)",
                        last_err, sleep_for, attempt + 1, retries,
                    )
                    time.sleep(sleep_for)
                    backoff = min(backoff * 2, 16.0)
                    continue

            # Non-recoverable
            raise ClaudeError(f"http {resp.status_code}: {resp.text[:500]}")

        except httpx.HTTPError as e:
            last_err = e
            if attempt < retries:
                logger.warning(
                    "[claude] network error %s, retrying in %.1fs (attempt %d/%d)",
                    e, backoff, attempt + 1, retries,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 16.0)
                continue
            raise ClaudeError(f"network: {e}") from e

    raise ClaudeError(f"exhausted retries: {last_err}")


def call_claude_json(
    prompt: str,
    system: Optional[str] = None,
    model: str = SONNET,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Convenience wrapper that asks Claude for strict JSON output and parses it.
    Adds a JSON-discipline reminder to the system prompt and falls back to
    extracting the first {...} block if Claude wraps the output in prose.
    """
    json_system = (
        (system or "")
        + "\n\nOUTPUT FORMAT: Respond with ONLY valid JSON. No markdown fences, no prose."
    )
    res = call_claude(
        prompt=prompt,
        system=json_system,
        model=model,
        max_tokens=max_tokens,
        **kwargs,
    )
    text = (res.get("text") or "").strip()
    # Strip markdown fences if Claude added them anyway
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        res["json"] = json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: find the first balanced JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                res["json"] = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                res["json"] = None
        else:
            res["json"] = None
    return res
