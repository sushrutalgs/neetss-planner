import os
from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
engine = create_engine(url)

sqls = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS exam_type VARCHAR(100) DEFAULT 'NEET_SS'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS leaderboard_opt_in BOOLEAN DEFAULT FALSE",
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS config_json JSON",
    """CREATE TABLE IF NOT EXISTS mcq_scores (
        id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        plan_id INTEGER REFERENCES plans(id) ON DELETE CASCADE, date DATE NOT NULL,
        topic VARCHAR(200) NOT NULL, subtopic VARCHAR(300), attempted INTEGER DEFAULT 0,
        correct INTEGER DEFAULT 0, time_minutes FLOAT, source VARCHAR(100), notes TEXT,
        created_at TIMESTAMP DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS study_sessions (
        id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        plan_id INTEGER REFERENCES plans(id) ON DELETE CASCADE, date DATE NOT NULL,
        topic VARCHAR(200), session_type VARCHAR(50) DEFAULT 'pomodoro',
        duration_minutes FLOAT NOT NULL, completed BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMP DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS daily_notes (
        id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        plan_id INTEGER REFERENCES plans(id) ON DELETE CASCADE, date DATE NOT NULL,
        note TEXT DEFAULT '', updated_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, plan_id, date))""",
    """CREATE TABLE IF NOT EXISTS recall_cards (
        id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        topic VARCHAR(200) NOT NULL, subtopic VARCHAR(300), ease_factor FLOAT DEFAULT 2.5,
        interval_days FLOAT DEFAULT 1, repetitions INTEGER DEFAULT 0,
        next_review_date DATE NOT NULL, last_quality INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, topic, subtopic))""",
    "CREATE INDEX IF NOT EXISTS ix_mcq_user_date ON mcq_scores(user_id, date)",
    "CREATE INDEX IF NOT EXISTS ix_mcq_user_topic ON mcq_scores(user_id, topic)",
    "CREATE INDEX IF NOT EXISTS ix_session_user_date ON study_sessions(user_id, date)",
    "CREATE INDEX IF NOT EXISTS ix_recall_next_review ON recall_cards(user_id, next_review_date)",
    # ───── Phase 2 — start_date/end_date plan window + replan tracking ─────
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS start_date DATE",
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS end_date DATE",
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS daily_minutes INTEGER",
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE",
    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS last_replan_at TIMESTAMP",
    "CREATE INDEX IF NOT EXISTS ix_plan_user_active ON plans(user_id, is_archived, end_date)",
    "CREATE INDEX IF NOT EXISTS ix_plan_start ON plans(start_date)",
    "CREATE INDEX IF NOT EXISTS ix_plan_end ON plans(end_date)",
]

with engine.connect() as conn:
    for s in sqls:
        try:
            conn.execute(text(s))
            print("OK:", s[:60])
        except Exception as e:
            print("SKIP:", str(e)[:80])
    conn.commit()
print("\nMigration complete!")
