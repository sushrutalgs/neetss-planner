"""
"What to improve next" recommender — Claude Haiku-backed.

The mastery vector tells us *which* topics are weak. The recommender turns
that into concrete, clickable next actions:

  "Watch the 18-min Adrenal physiology video, then 15 MCQs on
   pheochromocytoma — your accuracy on adrenal masses is 47% with 22
   attempts in the last 30 days."

It's intentionally tiered — Haiku for the high-volume per-user "give me 3
suggestions" call (cheap, called every Dashboard load), Sonnet only when
the user explicitly asks "explain why" via /api/ai-coach.

Public API:
    suggest_next_actions(user_signal, mastery_vector, bundle, n=3)
        -> [ { topic_id, topic_name, headline, action, why,
               minutes_est, content_refs[] } ]
"""
from __future__ import annotations
import json
import logging
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude, HAIKU, ClaudeError
from app.ai.mastery import rank_weakness

logger = logging.getLogger("planner.ai.recommender")


def _topic_lookup(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Flatten the bundle so we can look topics up by id without a tree walk."""
    out: Dict[str, Dict[str, Any]] = {}
    for cat in (bundle or {}).get("categories", []) or []:
        for sub in cat.get("subcategories", []) or []:
            for topic in sub.get("topics", []) or []:
                tid = str(topic.get("_id") or topic.get("topic_id") or "")
                if tid:
                    out[tid] = {
                        **topic,
                        "category_name": cat.get("name", ""),
                        "subcategory_name": sub.get("name", ""),
                    }
    return out


def _fallback_action(topic: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic action used when Claude is unavailable / errors."""
    counts = (topic or {}).get("content_counts", {}) or {}
    has_video = (counts.get("videos") or 0) > 0
    has_notes = (counts.get("notes") or 0) > 0
    has_mcq = (counts.get("mcqs") or 0) > 0
    pieces = []
    minutes = 0
    if has_video:
        pieces.append("watch the topic video")
        minutes += 18
    if has_notes:
        pieces.append("skim the notes")
        minutes += 15
    if has_mcq:
        pieces.append("attempt 15 MCQs")
        minutes += 20
    action = ", then ".join(pieces) if pieces else "review the topic outline"
    accuracy = (row or {}).get("drivers", {}).get("accuracy", 0)
    attempted = (row or {}).get("drivers", {}).get("attempted", 0)
    return {
        "topic_id": row.get("topic_id"),
        "topic_name": row.get("topic_name") or topic.get("name", ""),
        "category_name": (topic or {}).get("category_name", ""),
        "headline": f"Strengthen {row.get('topic_name') or topic.get('name', 'this topic')}",
        "action": action.capitalize(),
        "why": f"Your accuracy here is {accuracy}% over {attempted} attempts — recent gap is {row.get('gap', 0)}.",
        "minutes_est": minutes or 30,
        "content_refs": {
            "video_ids": (topic or {}).get("content_ids", {}).get("videos", [])[:1],
            "note_ids": (topic or {}).get("content_ids", {}).get("notes", [])[:1],
        },
        "source": "fallback",
    }


def suggest_next_actions(
    user_signal: Dict[str, Any],
    mastery_vector: Dict[str, Dict[str, Any]],
    bundle: Dict[str, Any],
    n: int = 3,
) -> List[Dict[str, Any]]:
    """
    Returns up to `n` ranked next-action recommendations.

    Strategy:
      1. Rank weakest topics from the blended mastery vector
      2. For each, pull content_counts + ids from the bundle
      3. Ask Claude Haiku for a short, motivating action plan
      4. If Claude errors, return the heuristic fallback
    """
    weakest = rank_weakness(mastery_vector, top_n=max(n, 3))
    if not weakest:
        # No mastery signal yet — recommend the first 3 P1 topics from the bundle.
        return _bootstrap_suggestions(bundle, n)

    topics_by_id = _topic_lookup(bundle)
    candidates = []
    for row in weakest[: n + 2]:  # ask for slightly more than n, dedupe
        tid = row.get("topic_id")
        topic = topics_by_id.get(str(tid), {})
        candidates.append({
            "row": row,
            "topic": topic,
            "topic_id": tid,
            "topic_name": row.get("topic_name") or topic.get("name", ""),
            "category": topic.get("category_name", ""),
            "subcategory": topic.get("subcategory_name", ""),
            "accuracy": row.get("drivers", {}).get("accuracy", 0),
            "attempted": row.get("drivers", {}).get("attempted", 0),
            "gap": row.get("gap", 0),
            "mastery": row.get("mastery", 0),
            "content_counts": topic.get("content_counts", {}),
        })

    # Build a compact JSON payload for Claude.
    payload = {
        "user_name": (user_signal or {}).get("user", {}).get("name", "Doctor"),
        "preparing_for": (user_signal or {}).get("user", {}).get("preparing_for"),
        "streak_current": (user_signal or {}).get("computed", {}).get("streak_current", 0),
        "avg_daily_minutes_14d": (user_signal or {}).get("computed", {}).get("avg_daily_minutes_14d", 0),
        "latest_predicted_rank": (user_signal or {}).get("computed", {}).get("latest_predicted_rank"),
        "weak_topics": [
            {
                "topic_id": str(c["topic_id"]),
                "topic_name": c["topic_name"],
                "category": c["category"],
                "subcategory": c["subcategory"],
                "accuracy_pct": c["accuracy"],
                "attempted": c["attempted"],
                "gap": c["gap"],
                "videos_available": c["content_counts"].get("videos", 0),
                "notes_available": c["content_counts"].get("notes", 0),
                "mcqs_available": c["content_counts"].get("mcqs", 0),
            }
            for c in candidates
        ],
        "n_requested": n,
    }

    system = (
        "You are a NEET SS surgical exam coach. The user is a senior surgical "
        "resident preparing for the Indian super-speciality entrance. Be specific, "
        "actionable, encouraging, and clinically grounded. NEVER invent content "
        "that doesn't exist in the available counts. Output JSON only."
    )
    prompt = (
        "Here is the student's current performance signal:\n\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        f"Generate exactly {n} next-action recommendations as a JSON array. "
        "Each item must have these exact fields:\n"
        "  topic_id (string), topic_name (string), headline (≤8 words),\n"
        "  action (1 sentence, imperative — what to do right now),\n"
        "  why (1 sentence — cite the weakness),\n"
        "  minutes_est (int), content_kinds (array subset of ['video','notes','mcq']).\n\n"
        "Pick the topics with the highest expected accuracy lift per minute. Do NOT "
        "include topics with zero available content. Return ONLY the JSON array, "
        "no prose, no markdown fences."
    )

    try:
        result = call_claude(
            prompt=prompt,
            system=system,
            model=HAIKU,
            max_tokens=1500,
            temperature=0.6,
        )
        text = (result.get("text") or "").strip()
        # Strip accidental fences if Haiku adds them.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Claude returned non-array")

        # Hydrate with content_refs from the bundle so the UI can deep-link.
        out: List[Dict[str, Any]] = []
        for item in parsed[:n]:
            tid = str(item.get("topic_id") or "")
            topic = topics_by_id.get(tid, {})
            content_ids = (topic or {}).get("content_ids", {}) or {}
            out.append({
                "topic_id": tid,
                "topic_name": item.get("topic_name") or topic.get("name", ""),
                "category_name": topic.get("category_name", ""),
                "headline": item.get("headline") or "Strengthen this topic",
                "action": item.get("action") or "Review the topic",
                "why": item.get("why") or "",
                "minutes_est": int(item.get("minutes_est") or 30),
                "content_refs": {
                    "video_ids": content_ids.get("videos", [])[:1],
                    "note_ids": content_ids.get("notes", [])[:1],
                },
                "content_kinds": item.get("content_kinds") or ["video", "mcq"],
                "source": "haiku",
            })
        if out:
            return out
    except (ClaudeError, ValueError, json.JSONDecodeError, KeyError) as e:
        logger.warning("[recommender] Claude failed, using fallback: %s", e)

    # Fallback to deterministic heuristic.
    return [_fallback_action(topics_by_id.get(str(c["topic_id"]), {}), c["row"]) for c in candidates[:n]]


def _bootstrap_suggestions(bundle: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
    """First-day suggestions when there's zero mastery signal yet — pick the
    earliest P1 topics with content available."""
    out: List[Dict[str, Any]] = []
    for cat in (bundle or {}).get("categories", []) or []:
        for sub in cat.get("subcategories", []) or []:
            for topic in sub.get("topics", []) or []:
                if (topic.get("priority_label") or "").upper().startswith("P1"):
                    counts = topic.get("content_counts", {}) or {}
                    if (counts.get("videos") or counts.get("notes") or counts.get("mcqs")):
                        content_ids = topic.get("content_ids", {}) or {}
                        out.append({
                            "topic_id": str(topic.get("_id") or ""),
                            "topic_name": topic.get("name", ""),
                            "category_name": cat.get("name", ""),
                            "headline": f"Start with {topic.get('name', 'this topic')}",
                            "action": "Watch the intro video, then attempt 10 MCQs to baseline yourself.",
                            "why": "P1 high-yield topic — building from here gives you the largest score lift early.",
                            "minutes_est": 35,
                            "content_refs": {
                                "video_ids": content_ids.get("videos", [])[:1],
                                "note_ids": content_ids.get("notes", [])[:1],
                            },
                            "content_kinds": ["video", "mcq"],
                            "source": "bootstrap",
                        })
                        if len(out) >= n:
                            return out
    return out
