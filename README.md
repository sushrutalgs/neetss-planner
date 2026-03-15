# Cortex Surgery AI Planner v3.0 — NEET SS Study Planner

The world's most advanced study planner for NEET SS (Super Specialty) surgical entrance exam preparation.

## What's New in v3.0

### Tier 1 — Quick Wins
- **Exam Countdown** — Live countdown (days/hours/minutes) on the planner dashboard
- **Study Heatmap** — GitHub-style 90-day heatmap showing daily study consistency
- **Pomodoro Timer** — Built-in 25/5 timer with topic tagging and session logging
- **MCQ Score Logging** — Per-session accuracy tracking (attempted/correct) by topic
- **Daily Notes** — Free-text notes field on each day card
- **Rest Days** — Configurable rest days per week (0/1/2) + custom dates
- **Custom Topic Weights** — Adjust priority weights during plan generation
- **PWA Support** — Installable on mobile homescreen with offline caching

### Tier 2 — Differentiators
- **Granular Syllabus Tree** — 20 topics × 120+ subtopics mapped to Bailey, Sabiston & Schwartz chapters
- **Weakness Heatmap** — Topic accuracy matrix coloured by performance (drives adaptive scheduling)
- **SM-2 Spaced Repetition** — Real SuperMemo algorithm replaces fixed 1-3-5-7-9 pattern
- **Analytics Dashboard** — Study hours trends, MCQ accuracy by topic, predicted score model
- **Predicted Score Model** — Estimated performance range based on coverage, accuracy, and P1 mastery
- **Syllabus Browser** — Full syllabus with textbook references accessible from the app

### Tier 3 — Moat Builders
- **AI Adaptive Engine** — Regenerates remaining schedule based on weakness data
- **AI Weekly Coach** — Personalized weekly analysis: weak topics, recommendations, stats
- **Peer Leaderboard** — Anonymous, opt-in rankings by streak, accuracy, coverage, hours
- **Multi-Exam Support** — Registration supports NEET SS, INI SS, FRCS, MRCS
- **Weighted Topic Rotation** — P1 topics appear 3× more often than P3 (was flat round-robin)

## Architecture

### Backend
- **Framework**: FastAPI 0.115
- **Database**: PostgreSQL (SQLAlchemy ORM) / SQLite for local dev
- **Auth**: JWT with bcrypt password hashing
- **Deploy**: Heroku with gunicorn

### API Endpoints (11 routers)

| Router | Prefix | Endpoints |
|---|---|---|
| Users | `/api` | register, login, me, update profile |
| Plans | `/api` | generate plan, save, list, get, delete, download (ICS/PDF), syllabus |
| Progress | `/api` | get/update per-plan progress |
| MCQ Scores | `/api` | log scores, list, topic summary |
| Study Sessions | `/api` | log pomodoro, list, daily totals (heatmap) |
| Notes | `/api` | upsert/get daily notes |
| Recall | `/api` | due cards, review (SM-2), stats |
| Analytics | `/api` | full summary, weakness map |
| Leaderboard | `/api` | ranked entries |
| AI Coach | `/api` | weekly review, adapt plan |
| Admin | `/api/admin` | stats, CSV export |

### Database Models (7 tables)
- `users` — Auth + profile + leaderboard opt-in
- `plans` — Saved plan data + config
- `progress` — Per-plan checkbox progress
- `mcq_scores` — Per-session MCQ accuracy log
- `study_sessions` — Pomodoro/free study time log
- `daily_notes` — Free-text notes per day
- `recall_cards` — SM-2 spaced repetition state

### Frontend
- Single-page vanilla HTML/CSS/JS (no build step)
- 11-tab interface: Planner, Weekly, Daily, Progress, Analytics, Timer, Recall, AI Coach, Leaderboard, Syllabus, Saved
- Apple-quality UI with dark mode
- PWA with service worker for offline support

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
heroku create cortexsurgery
heroku addons:create heroku-postgresql:essential-0
heroku config:set JWT_SECRET=your-secret-here
heroku config:set ALLOWED_ORIGINS=https://www.cortexsurgery.ai
git push heroku main
```

## Syllabus Coverage

20 topics across 3 priority tiers:
- **P1 HIGH (48%)**: Breast, Thyroid & Parathyroid, Head & Neck, Adrenal, Cardiac Surgery, Thoracic Surgery, Vascular Surgery, Plastic Surgery & Burns
- **P2 MODERATE (36%)**: Basic Principles, Pediatric Surgery, Perioperative Care, Trauma, Genitourinary, Transplant, Neurosurgery
- **P3 SUPPORT (16%)**: GIT Upper, GIT Lower, GIT HPB, GIT Misc

Each topic has 5-8 sub-chapters mapped to specific chapters in Bailey & Love, Sabiston, and Schwartz.

---

Built by Sushruta Educations LLP · cortexsurgery.ai
