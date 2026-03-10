# Cortex Surgery AI Planner — NEET SS Study Planner

A full-stack study planner for NEET SS (Super Specialty) surgical entrance exam preparation.

## Features

- **Smart Schedule Generation** — Phases (Foundation → Consolidation → Revision) with weighted topic allocation
- **Interleaved Learning** — MCQ topics differ from theory topics to boost retention
- **Spaced Recall** — 1-3-5-7-9 day recall pattern built into the schedule
- **Mock Exam Scheduling** — Evenly distributed mock exams with dedicated analysis time
- **Progress Tracking** — Per-day checkboxes with donut chart visualisation, synced to server
- **Plan Export** — Download plans as ICS (calendar) or PDF
- **Admin Dashboard** — View registered users, plans, and export CSV

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy + PostgreSQL (SQLite locally)
- **Auth**: JWT (PyJWT) + bcrypt
- **Frontend**: Vanilla HTML/CSS/JS (single-page)
- **Deploy**: Heroku

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./planner.db` | Database connection string |
| `JWT_SECRET` | *(required)* | Secret key for JWT signing |
| `JWT_TTL_MIN` | `43200` | Token expiry in minutes (default 30 days) |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `ADMIN_EMAIL` | `sushrutalgs@gmail.com` | Admin email for dashboard access |

## Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Deploy to Heroku

```bash
heroku create
heroku addons:create heroku-postgresql:essential-0
heroku config:set JWT_SECRET=your-secret-here
heroku config:set ALLOWED_ORIGINS=https://www.cortexsurgery.ai
git push heroku main
```
