"""
Snapshot Series PDF generator — auto-summary revision sheets.

Two flavours:
  • generate_topic_snapshot(topic_name, content) — single-topic 2-page brief
  • generate_weekly_snapshot(plan, week_days)    — week-in-review summary

Both call Sonnet for the markdown content, then ReportLab to lay out a
2-column A4 page. ReportLab is the only PDF dep and is already lightweight
enough for the existing PDF export route.

The output PDF is written to ./uploads/snapshots/<user_id>-<slug>.pdf and
the path is returned. The caller is responsible for serving it via the
existing /api/files static handler.
"""
from __future__ import annotations
import logging
import os
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.ai.claude import call_claude, SONNET, ClaudeError

logger = logging.getLogger("planner.ai.revision_pdf")

SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "snapshots")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (s or "").lower()).strip("-")[:60]


def _ensure_dir() -> None:
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


def generate_topic_snapshot(
    user_id: int,
    topic_name: str,
    note_chunks: Optional[List[Dict[str, Any]]] = None,
    mastery_drivers: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a single-topic Snapshot. Sonnet generates 600 words of structured
    revision content; ReportLab paginates it onto a styled A4 PDF.
    """
    _ensure_dir()
    note_chunks = note_chunks or []

    prompt_payload = {
        "topic": topic_name,
        "drivers": mastery_drivers or {},
        "source_excerpts": [
            (c.get("text") or "")[:500] for c in note_chunks[:6]
        ],
    }
    system = (
        "You are Cortex, generating a one-page revision Snapshot for a NEET SS "
        "surgical resident. Distill the topic into a high-yield brief. Use the "
        "source_excerpts as the only factual ground truth — do not invent. "
        "Output markdown only."
    )
    prompt = (
        f"Topic data:\n{prompt_payload}\n\n"
        "Generate a markdown brief with these sections:\n"
        "  ## Snapshot\n  (2 sentences — what this topic is)\n"
        "  ## High-Yield Facts\n  (6-10 bullets)\n"
        "  ## Key Comparisons\n  (1-3 mini-tables in markdown)\n"
        "  ## Common Pitfalls\n  (3 bullets)\n"
        "  ## 2-Min Recall\n  (3 questions with 1-line answers)\n"
        "Total ~550 words."
    )

    try:
        result = call_claude(prompt=prompt, system=system, model=SONNET, max_tokens=2200, temperature=0.5)
        markdown = (result.get("text") or "").strip()
    except ClaudeError as e:
        logger.warning("[revision_pdf] Sonnet failed: %s", e)
        markdown = f"# {topic_name}\n\n_Generation failed — try again later._"

    pdf_path = os.path.join(SNAPSHOTS_DIR, f"{user_id}-{_slug(topic_name)}-{date.today().isoformat()}.pdf")
    _render_markdown_pdf(markdown, pdf_path, title=f"Snapshot · {topic_name}")
    return {
        "path": pdf_path,
        "filename": os.path.basename(pdf_path),
        "topic": topic_name,
        "markdown": markdown,
        "generated_at": datetime.utcnow().isoformat(),
    }


def generate_weekly_snapshot(
    user_id: int,
    week_days: List[Dict[str, Any]],
    weekly_signal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Week-in-review Snapshot — what the user covered + what's left."""
    _ensure_dir()
    payload = {
        "days": week_days[:7],
        "signal": weekly_signal or {},
    }
    system = (
        "You are Cortex, writing a weekly snapshot for a NEET SS surgical "
        "resident. Tone: clinical, concise, encouraging. Output markdown only."
    )
    prompt = (
        f"Week data:\n{payload}\n\n"
        "Markdown sections:\n"
        "  ## This Week at a Glance (2 sentences)\n"
        "  ## What You Covered (bullets, group by topic)\n"
        "  ## What Moved Most (top 3 mastery deltas)\n"
        "  ## What's Slipping (1-2 risks)\n"
        "  ## Next Week's Priority (3 concrete actions)\n"
        "Total ~500 words."
    )
    try:
        result = call_claude(prompt=prompt, system=system, model=SONNET, max_tokens=2000, temperature=0.5)
        markdown = (result.get("text") or "").strip()
    except ClaudeError as e:
        logger.warning("[revision_pdf] Sonnet failed: %s", e)
        markdown = "# Weekly Snapshot\n\n_Generation failed — try again later._"

    pdf_path = os.path.join(
        SNAPSHOTS_DIR, f"{user_id}-week-{date.today().isoformat()}.pdf"
    )
    _render_markdown_pdf(markdown, pdf_path, title=f"Weekly Snapshot · {date.today().isoformat()}")
    return {
        "path": pdf_path,
        "filename": os.path.basename(pdf_path),
        "markdown": markdown,
        "generated_at": datetime.utcnow().isoformat(),
    }


# ───────────────────────── ReportLab renderer ─────────────────────────


def _render_markdown_pdf(markdown: str, out_path: str, title: str = "Snapshot") -> None:
    """
    Lightweight markdown→PDF renderer. Handles H1/H2/H3, bullets, plain
    paragraphs, and very simple tables (rows separated by `|`). Anything
    fancier degrades to plain text.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
        from reportlab.lib import colors
    except ImportError:
        # ReportLab not installed — write markdown as a .md sidecar so
        # the caller can still serve something.
        with open(out_path.replace(".pdf", ".md"), "w", encoding="utf-8") as f:
            f.write(markdown)
        return

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="Cortex Surgery Planner",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=colors.HexColor("#0a2540"), spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor("#0071e3"), spaceAfter=6)
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], textColor=colors.HexColor("#1d1d1f"), spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=13, spaceAfter=4)
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=4)

    flow: List[Any] = [Paragraph(title, h1), Spacer(1, 6)]
    table_buf: List[List[str]] = []

    def flush_table():
        if table_buf:
            t = Table(table_buf, hAlign="LEFT")
            t.setStyle(TableStyle([
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f7")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#d2d2d7")),
                ("INNERGRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#e5e5ea")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]))
            flow.append(t)
            flow.append(Spacer(1, 6))
            table_buf.clear()

    for raw_line in (markdown or "").split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            flush_table()
            flow.append(Spacer(1, 4))
            continue
        if line.startswith("# "):
            flush_table()
            flow.append(Paragraph(line[2:], h1))
        elif line.startswith("## "):
            flush_table()
            flow.append(Paragraph(line[3:], h2))
        elif line.startswith("### "):
            flush_table()
            flow.append(Paragraph(line[4:], h3))
        elif line.lstrip().startswith(("- ", "* ")):
            flush_table()
            flow.append(Paragraph("• " + line.lstrip()[2:], bullet))
        elif "|" in line and line.count("|") >= 2:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # markdown table separator
            table_buf.append(cells)
        else:
            flush_table()
            flow.append(Paragraph(line, body))

    flush_table()
    doc.build(flow)
