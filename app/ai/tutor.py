"""
Conversational AI tutor — Claude Sonnet with RAG + planner-state context.

Two surfaces:
  1. /api/ai/coach/chat   — multi-turn back-and-forth, persists ChatMessage rows
  2. /api/ai/coach/ask    — one-shot question, no memory

The tutor is grounded in three context blocks (in priority order):
  • The user's mastery vector + weakest 5 topics (so "what should I revise?"
    answers itself).
  • Their last 7 days of MCQ activity (so "why did I bomb adrenal yesterday?"
    actually has data to chew on).
  • A small RAG window from NoteChunk embeddings, queried by the user's
    question via cosine similarity (top-k=4).

Memory: the last 12 turns are kept verbatim, older turns get a 1-line
summary appended to a rolling system note.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.ai.claude import call_claude, SONNET, HAIKU, ClaudeError

logger = logging.getLogger("planner.ai.tutor")


SYSTEM_PROMPT = """You are Cortex, a NEET SS surgical exam coach embedded in
the Cortex Surgery Planner. You are talking to a senior surgical resident
preparing for the Indian super-speciality entrance.

Voice:
  • Direct, concise, clinically grounded. No fluff, no "great question!".
  • Use real surgical reasoning — anatomy, physiology, evidence.
  • When the user asks "what should I do next" — give them ONE concrete
    action, not a list. Reference their actual data ("your adrenal accuracy
    is 47%" not "you might want to review adrenal").
  • Cite the topic name and content kind when you reference their study
    plan. NEVER invent content that doesn't exist in the planner_context.

Output: plain markdown. Short paragraphs. No emoji.
"""


def _format_context(planner_context: Dict[str, Any]) -> str:
    """Render the user's planner state as a compact, Sonnet-readable block."""
    if not planner_context:
        return ""
    weakest = planner_context.get("weakest_topics") or []
    streak = planner_context.get("streak_current", 0)
    avg_min = planner_context.get("avg_daily_minutes_14d", 0)
    rank = planner_context.get("latest_predicted_rank")
    today = planner_context.get("today_block_summary", "")
    coverage = planner_context.get("coverage_pct", 0)
    avg_mastery = planner_context.get("avg_mastery", 0)

    weak_lines = []
    for w in weakest[:5]:
        weak_lines.append(
            f"  - {w.get('topic_name', 'unknown')}: mastery {round(100*(w.get('mastery') or 0))}%, "
            f"accuracy {w.get('drivers', {}).get('accuracy', 0)}% over "
            f"{w.get('drivers', {}).get('attempted', 0)} attempts"
        )

    return (
        "<planner_context>\n"
        f"Streak: {streak} days. Avg daily minutes (14d): {avg_min}. "
        f"Latest predicted rank: {rank or 'n/a'}.\n"
        f"Coverage: {round(coverage*100)}%. Average mastery: {round(avg_mastery*100)}%.\n"
        f"Today's plan: {today or 'no day card'}.\n"
        f"Weakest topics:\n" + "\n".join(weak_lines) + "\n"
        "</planner_context>"
    )


def _format_rag(chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return ""
    lines = ["<reference_notes>"]
    for c in chunks[:4]:
        lines.append(f"[{c.get('topic_name', 'note')}] {(c.get('text') or '')[:600]}")
    lines.append("</reference_notes>")
    return "\n".join(lines)


def chat(
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    planner_context: Optional[Dict[str, Any]] = None,
    rag_chunks: Optional[List[Dict[str, Any]]] = None,
    model: str = SONNET,
) -> Dict[str, Any]:
    """
    Multi-turn turn handler. `history` is a list of {role, content} dicts in
    chronological order (last 12 turns). Returns:
        { text, model, input_tokens, output_tokens, latency_ms }
    """
    history = history or []
    context_block = _format_context(planner_context or {})
    rag_block = _format_rag(rag_chunks or [])

    system = SYSTEM_PROMPT
    if context_block or rag_block:
        system += "\n\n" + context_block + "\n" + rag_block

    # Trim history to last 12 turns and prepend any older summary.
    turns = history[-12:]
    older = history[:-12]
    if older:
        summary = " | ".join((t.get("content") or "")[:60] for t in older[-6:])
        system += f"\n\n<earlier_chat_summary>{summary}</earlier_chat_summary>"

    # The Claude wrapper takes a single prompt — we serialise the chat into
    # an interleaved transcript with explicit role tags.
    transcript_lines = []
    for t in turns:
        role = "USER" if t.get("role") == "user" else "TUTOR"
        transcript_lines.append(f"{role}: {t.get('content', '')}")
    transcript_lines.append(f"USER: {user_message}")
    transcript_lines.append("TUTOR:")
    prompt = "\n".join(transcript_lines)

    try:
        result = call_claude(
            prompt=prompt,
            system=system,
            model=model,
            max_tokens=2400,
            temperature=0.7,
        )
        return {
            "text": (result.get("text") or "").strip(),
            "model": model,
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
            "latency_ms": result.get("latency_ms"),
        }
    except ClaudeError as e:
        logger.warning("[tutor.chat] Claude failed: %s", e)
        return {
            "text": (
                "I'm having trouble reaching the AI service right now. "
                "Try again in a moment — your plan and progress are safe."
            ),
            "model": model,
            "input_tokens": 0,
            "output_tokens": 0,
            "error": str(e),
        }


def ask(question: str, planner_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One-shot ask — used by the daily-briefing 'ask a follow-up' button."""
    return chat(user_message=question, history=[], planner_context=planner_context)


def explain_weakness(
    topic_name: str,
    drivers: Dict[str, Any],
    related_questions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    "Explain why I'm weak on X" — Sonnet, deeper than the chat surface,
    cites specific drivers from the mastery vector.
    """
    payload = {
        "topic": topic_name,
        "accuracy_pct": drivers.get("accuracy", 0),
        "attempted": drivers.get("attempted", 0),
        "days_since_last": drivers.get("days_since_last", 999),
        "coverage_pct": drivers.get("coverage", 0),
        "recent_misses": (related_questions or [])[:6],
    }
    system = SYSTEM_PROMPT + "\nThis is a weakness deep-dive. Be specific about *why* the gap exists."
    prompt = (
        f"Analyse this weakness:\n\n{json.dumps(payload, indent=2)}\n\n"
        "Output 3 short markdown sections:\n"
        "  ### What the data says (1 paragraph)\n"
        "  ### Likely root cause (1 paragraph, clinical reasoning)\n"
        "  ### Next 60 minutes (3 bullets, concrete actions)\n"
    )
    try:
        result = call_claude(prompt=prompt, system=system, model=SONNET, max_tokens=1500, temperature=0.6)
        return {"markdown": result.get("text", "").strip(), "model": SONNET}
    except ClaudeError as e:
        logger.warning("[tutor.explain_weakness] failed: %s", e)
        return {"markdown": "Unable to generate analysis right now.", "error": str(e)}
