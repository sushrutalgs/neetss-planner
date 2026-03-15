# Cortex Surgery AI Planner — NEET SS Study Planner v3

## Features
- **Apple-inspired minimal UI** — clean white/dark theme, frosted glass header, smooth animations
- **Dashboard** — exam countdown, study hours, streak, today's plan at a glance
- **Smart Schedule Generation** — Foundation → Consolidation → Revision phases with weighted topics
- **Interleaved Learning** — MCQ topics differ from theory for better retention
- **Spaced Recall** — 1-3-5-7-9 day pattern built into the schedule
- **Mock Scheduling** — evenly distributed mocks with analysis time
- **Progress Tracking** — per-day checkboxes, donut charts, streak counter
- **Password Reset** — forgot password flow with admin-managed reset codes
- **Plan Export** — download as ICS calendar or PDF
- **Admin Dashboard** — user/plan stats, CSV export, password reset management

## Tech Stack
FastAPI · SQLAlchemy · PostgreSQL · JWT · bcrypt · ReportLab · Vanilla JS

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./planner.db` | Database URL |
| `JWT_SECRET` | *(required)* | JWT signing secret |
| `ALLOWED_ORIGINS` | `*` | CORS origins (comma-separated) |
| `ADMIN_EMAIL` | `sushrutalgs@gmail.com` | Admin email |

## Deploy
```bash
heroku config:set JWT_SECRET=your-secret ALLOWED_ORIGINS=https://www.cortexsurgery.ai
git push heroku main
```
