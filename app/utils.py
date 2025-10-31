from __future__ import annotations
from datetime import datetime
from typing import Dict, Any, List
from io import BytesIO

# --- ICS generation (simple) --- #
def plan_to_ics(plan: Dict[str, Any]) -> bytes:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sushruta LGS//NEET SS Planner//EN"]
    schedule: List[Dict[str, Any]] = plan.get("schedule", [])
    # naive: set all events at 08:00–10:00 local (convert to floating Z naive)
    for day in schedule:
        date_str = day["date"].replace("-", "")
        # event summary
        if day.get("is_mock_day"):
            summary = "NEET SS – Mock + Analysis"
        else:
            parts = []
            if day["theory"]["minutes"] > 0:
                parts.append(f"Theory: {day['theory']['topic']}")
            if day["mcq"]["minutes"] > 0:
                parts.append(f"MCQ: {day['mcq']['topic']}")
            parts.append("Recall")
            summary = " | ".join(parts)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{date_str}@neetss",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{date_str}T080000Z",
            f"DTEND:{date_str}T100000Z",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return ("\n".join(lines)).encode("utf-8")

# --- Simple PDF (using reportlab) --- #
def plan_to_pdf(plan: Dict[str, Any]) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm
    except Exception:
        # If reportlab isn't available, return a basic text blob as PDF bytes substitute
        return b"%PDF-1.4\n% Basic PDF generation not available.\n"

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    y = height - margin

    meta = plan.get("meta", {})
    title = f"NEET SS Study Plan ({meta.get('start_date')} → {meta.get('exam_date')})"
    c.setFont("Helvetica-Bold", 14); c.drawString(margin, y, title); y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(margin, y, f"Hours/Day: {meta.get('hours_per_day')} | Mocks: {len(meta.get('mock_days_indexed', []))}"); y -= 14

    c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Weekly Overview"); y -= 16
    for w in plan.get("weekly_summaries", [])[:10]:
        line = f"W{w['week']} {w['start_date']}→{w['end_date']} | {w['phase']} | Mocks: {w['mocks']}"
        c.setFont("Helvetica", 10); c.drawString(margin, y, line); y -= 12
        if y < margin: c.showPage(); y = height - margin

    c.setFont("Helvetica-Bold", 12); c.drawString(margin, y, "Daily Plan (first 14 days)"); y -= 16
    for d in plan.get("schedule", [])[:14]:
        tag = "MOCK" if d.get("is_mock_day") else ""
        line = f"{d['date']} {tag} | Theory {d['theory']['topic']} {d['theory']['minutes']}m | MCQ {d['mcq']['topic']} {d['mcq']['minutes']}m | Recall {d['recall']['minutes']}m"
        c.setFont("Helvetica", 9); c.drawString(margin, y, line[:110]); y -= 12
        if y < margin: c.showPage(); y = height - margin

    c.showPage(); c.save()
    return buf.getvalue()

