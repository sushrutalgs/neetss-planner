# NEET SS Study Planner (FastAPI + Heroku)

## Features
- Rule of 3 phases (initial 70% @ 35/45/20; middle consolidation; last 15 days heavy revision)
- Interleaved design (theory topic ≠ MCQ topic)
- Spaced recall 1–3–5–7–9
- Mock placement: D+7 and D−10, others evenly spaced
- MCQ targets computed at ~2.5 min/MCQ (configurable)
- Weekly summaries

## Local run
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

Open http://127.0.0.1:8000 and serve `static/index.html` (or mount via nginx).  
For a quick dev test, open `static/index.html` and change `fetch('/plan', ...)` to your localhost URL: `http://127.0.0.1:8000/plan`.

## Heroku (via GitHub)
1. Create new Heroku app → Connect to GitHub repo → Enable automatic deploys (optional).
2. Set Buildpacks (Python is auto-detected via `requirements.txt`).
3. Deploy branch.
4. Add a Static host (e.g., GitHub Pages or a simple nginx) OR configure a small static file server; alternatively host `index.html` on any static host and point it to `https://<your-heroku-app>.herokuapp.com/plan`.

## Optional: Serve index.html from FastAPI
- Add `from fastapi.staticfiles import StaticFiles`
- `app.mount("/static", StaticFiles(directory="static"), name="static")`
- Then open `https://<app>/static/index.html`
