from __future__ import annotations
from datetime import datetime
from typing import Dict, Any, List
from io import BytesIO


# --------------------------------------------------------------------------- #
#                           ICS CALENDAR EXPORT
# --------------------------------------------------------------------------- #

def plan_to_ics(plan: Dict[str, Any]) -> bytes:
    """Generate an ICS calendar file from a plan."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sushruta LGS//NEET SS Planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    schedule: List[Dict[str, Any]] = plan.get("schedule", [])

    for day in schedule:
        date_str = day["date"].replace("-", "")
        now_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

        # Build summary from hours (not minutes)
        if day.get("is_mock_day"):
            mock_info = day.get("mock", {})
            summary = f"NEET SS Mock ({mock_info.get('hours', 2.5)}h) + Analysis ({mock_info.get('analysis_hours', 1)}h)"
        else:
            parts = []
            theory = day.get("theory", {})
            mcq = day.get("mcq", {})
            recall = day.get("recall", {})

            if theory.get("hours", 0) > 0:
                parts.append(f"Theory: {theory['topic']} ({theory['hours']}h)")
            if mcq.get("hours", 0) > 0:
                parts.append(f"MCQ: {mcq['topic']} ({mcq['hours']}h, {mcq.get('target_questions', 0)}Qs)")
            if recall.get("hours", 0) > 0:
                topics = ", ".join(recall.get("due_topics", [])) or "Review"
                parts.append(f"Recall: {topics} ({recall['hours']}h)")
            summary = " | ".join(parts) if parts else "Study Day"

        # Build description
        description = f"Phase: {day.get('phase', '')}"

        lines += [
            "BEGIN:VEVENT",
            f"UID:{date_str}-neetss@cortexsurgery.ai",
            f"DTSTAMP:{now_stamp}",
            f"DTSTART;VALUE=DATE:{date_str}",
            f"DTEND;VALUE=DATE:{date_str}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode("utf-8")


def _ics_escape(text: str) -> str:
    """Escape special characters for ICS format."""
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


# --------------------------------------------------------------------------- #
#                              PDF EXPORT
# --------------------------------------------------------------------------- #

def plan_to_pdf(plan: Dict[str, Any]) -> bytes:
    """Generate a PDF summary from a plan using reportlab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor
    except ImportError:
        return _fallback_pdf(plan)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    y = height - margin

    meta = plan.get("meta", {})

    # ---- Title ----
    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(HexColor("#1e3a8a"))
    c.drawString(margin, y, "NEET SS Study Plan")
    y -= 22

    c.setFont("Helvetica", 11)
    c.setFillColor(HexColor("#334155"))
    c.drawString(margin, y, f"{meta.get('start_date', '?')}  →  {meta.get('exam_date', '?')}   |   "
                            f"{meta.get('days', '?')} days   |   "
                            f"{meta.get('hours_per_day', '?')} h/day   |   "
                            f"{len(meta.get('mock_days_indexed', []))} mocks")
    y -= 24

    # ---- Weekly Overview ----
    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(HexColor("#1e3a8a"))
    c.drawString(margin, y, "Weekly Overview")
    y -= 18

    c.setFont("Helvetica", 9)
    c.setFillColor(HexColor("#0f172a"))

    for w in plan.get("weekly_summaries", []):
        targets = w.get("weekly_targets", {})
        mocks = w.get("mocks", 0)
        line = (f"W{w['week']}  {w['start_date']} → {w['end_date']}  |  {w['phase']}  |  "
                f"Theory {targets.get('theory_hr', 0)}h  MCQ {targets.get('mcq_hr', 0)}h  "
                f"Recall {targets.get('recall_hr', 0)}h  ~{targets.get('approx_mcqs', 0)} MCQs"
                f"{'  🧾 ' + str(mocks) + ' mock(s)' if mocks else ''}")
        c.drawString(margin, y, line[:120])
        y -= 13
        if y < margin + 20:
            c.showPage()
            y = height - margin

    y -= 10

    # ---- Daily Plan (first 21 days) ----
    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(HexColor("#1e3a8a"))
    c.drawString(margin, y, "Daily Plan (first 21 days)")
    y -= 18

    c.setFont("Helvetica", 8.5)
    c.setFillColor(HexColor("#0f172a"))

    for d in plan.get("schedule", [])[:21]:
        theory = d.get("theory", {})
        mcq = d.get("mcq", {})
        recall = d.get("recall", {})
        mock_tag = " 🧾 MOCK" if d.get("is_mock_day") else ""

        line = (f"{d['date']}{mock_tag}  |  "
                f"Theory: {theory.get('topic', '-')} ({theory.get('hours', 0)}h)  |  "
                f"MCQ: {mcq.get('topic', '-')} ({mcq.get('hours', 0)}h, {mcq.get('target_questions', 0)}Qs)  |  "
                f"Recall: {recall.get('hours', 0)}h")

        c.drawString(margin, y, line[:130])
        y -= 12
        if y < margin + 20:
            c.showPage()
            y = height - margin

    # ---- Footer ----
    y -= 16
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(HexColor("#64748b"))
    c.drawString(margin, y, f"Generated by Cortex Surgery AI Planner · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")

    c.showPage()
    c.save()
    return buf.getvalue()


def _fallback_pdf(plan: Dict[str, Any]) -> bytes:
    """Minimal PDF when reportlab is not available."""
    meta = plan.get("meta", {})
    text = (f"NEET SS Study Plan\n"
            f"{meta.get('start_date')} to {meta.get('exam_date')}\n"
            f"{meta.get('days')} days, {meta.get('hours_per_day')} h/day\n\n"
            f"Install reportlab for a proper PDF export.\n")
    # Minimal valid PDF structure
    content = text.encode("latin-1")
    stream = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n0\n%%EOF\n"
    )
    return stream
