"""
Daily briefing — the morning "what's the plan today" message.

Cheap Haiku call (it's hit on every Dashboard load), with a deterministic
fallback so the Dashboard never renders an empty briefing card.

Output structure:
    {
      "greeting":   "Good morning, Dr. Sharma — day 47 of your plan.",
      "headline":   "Adrenal physiology block this morning, 3 mocks at 6pm.",
      "focus":      "Your accuracy on pheo dropped 8% this week.",
      "blocks":     [{title, minutes, kind, why}],
      "encouragement": "You're 12 days ahead of pace. Keep going."
    }
"""
from __future__ import annotations
import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude, HAIKU, ClaudeError

logger = logging.getLogger("planner.ai.daily_briefing")


def _fallback_briefing(
    user_name: str,
    today_blocks: List[Dict[str, Any]],
    streak: int,
    avg_minutes_14d: float,
) -> Dict[str, Any]:
    minutes_today = sum((b.get("estimated_minutes") or 0) for b in today_blocks)
    block_summaries = [
        {
            "title": b.get("title") or b.get("topic_name") or "Study block",
            "minutes": int(b.get("estimated_minutes") or 0),
            "kind": b.get("kind") or "mixed",
            "why": "On your plan for today.",
        }
        for b in today_blocks[:5]
    ]
    return {
        "greeting": f"Good morning, {user_name or 'Doctor'} — day {streak or 1} streak.",
        "headline": f"{minutes_today} minutes scheduled today across {len(today_blocks)} blocks.",
        "focus": "Stick with the plan — it's tuned to your last 14 days.",
        "blocks": block_summaries,
        "encouragement": (
            "You're averaging "
            f"{round(avg_minutes_14d)} min/day. "
            "Consistency beats intensity."
        ),
        "source": "fallback",
        "generated_at": datetime.utcnow().isoformat(),
    }


def generate(
    user_name: str,
    today_blocks: List[Dict[str, Any]],
    weakest_topics: List[Dict[str, Any]],
    streak_current: int,
    avg_minutes_14d: float,
    days_to_exam: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build the morning briefing. Falls back to deterministic copy on Claude
    failure so the dashboard always renders.
    """
    payload = {
        "name": user_name or "Doctor",
        "today_iso": date.today().isoformat(),
        "today_blocks": [
            {
                "title": b.get("title") or b.get("topic_name", "block"),
                "minutes": int(b.get("estimated_minutes") or 0),
                "kind": b.get("kind", "mixed"),
            }
            for b in today_blocks[:6]
        ],
        "weakest_topics": [
            {
                "topic_name": w.get("topic_name", "?"),
                "accuracy_pct": w.get("drivers", {}).get("accuracy", 0),
                "gap": w.get("gap", 0),
            }
            for w in (weakest_topics or [])[:3]
        ],
        "streak_current": streak_current,
        "avg_minutes_14d": round(avg_minutes_14d, 1),
        "days_to_exam": days_to_exam,
    }

    system = (
        "You are Cortex, a NEET SS surgical exam coach writing a morning briefing. "
        "Tone: warm but direct, like a senior consultant texting a resident. No emoji. "
        "No fluff. Cite specific numbers from the payload. Output JSON only."
    )
    prompt = (
        f"Write today's briefing for this resident:\n\n{json.dumps(payload, indent=2)}\n\n"
        "Return JSON with these exact fields: greeting (1 sentence, addresses by name + day count), "
        "headline (1 sentence, what the day looks like), focus (1 sentence, the *one* thing that "
        "matters most today, must reference a real weak topic if any), blocks (array of "
        "{title, minutes, kind, why} — 'why' is a half-sentence), encouragement (1 sentence). "
        "Only JSON, no prose, no fences."
    )

    try:
        result = call_claude(
            prompt=prompt,
            system=system,
            model=HAIKU,
            max_tokens=900,
            temperature=0.7,
        )
        text = (result.get("text") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        parsed = json.loads(text)
        parsed["source"] = "haiku"
        parsed["generated_at"] = datetime.utcnow().isoformat()
        return parsed
    except (ClaudeError, ValueError, json.JSONDecodeError, KeyError) as e:
        logger.warning("[daily_briefing] Claude failed: %s", e)
        return _fallback_briefing(user_name, today_blocks, streak_current, avg_minutes_14d)
