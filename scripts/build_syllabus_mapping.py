"""
One-shot syllabus mapping job.

Aligns the planner's hardcoded SYLLABUS_TREE (Sabiston/Schwartz/Bailey
chapter taxonomy) with the LMS Mongo hierarchy (admin-curated
category → subcategory → topic) via Claude Sonnet 4.6.

Run modes:
  python -m scripts.build_syllabus_mapping              # full re-run, all topics
  python -m scripts.build_syllabus_mapping --dry-run    # write to stdout, do not persist
  python -m scripts.build_syllabus_mapping --topic Thyroid   # only re-map this planner topic

Outputs are persisted to the planner Postgres `syllabus_mapping` table.
The LMS-side admin UI in restructurer.js can override individual rows;
this script will NOT overwrite rows where source='manual'.

Required environment:
  ANTHROPIC_API_KEY        — already in your tier-4 setup
  LMS_BASE_URL             — e.g. https://api.sushrutalgs.in
  LMS_ADMIN_TOKEN          — admin JWT minted by the LMS so we can fetch
                             the entire hierarchy regardless of subscription
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

import httpx

# Make the script runnable as `python -m scripts.build_syllabus_mapping`
# AND as `python scripts/build_syllabus_mapping.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.claude import call_claude_json, SONNET  # noqa: E402
from app.database import SessionLocal               # noqa: E402
from app.models import SyllabusMapping              # noqa: E402
from app.priorities import SYLLABUS_TREE            # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
log = logging.getLogger("syllabus_mapping")

LMS_BASE = os.getenv("LMS_BASE_URL", "").rstrip("/")
LMS_ADMIN_TOKEN = os.getenv("LMS_ADMIN_TOKEN", "")


SYSTEM_PROMPT = """\
You are mapping a hardcoded surgical-textbook taxonomy onto a learning
platform's content hierarchy. The textbook taxonomy lists topics and
subtopics anchored to Sabiston/Schwartz/Bailey chapters. The LMS hierarchy
is curated by admins and groups topics into Categories → Subcategories →
Topics, where each Topic is the leaf the user actually studies.

For every (planner_topic, planner_subtopic) pair you receive, return the
matching LMS topic_ids — the leaves whose subject matter overlaps with
the planner subtopic. Many-to-many is fine: a planner subtopic may map
to 0, 1, or several LMS topics, and an LMS topic may match several
planner subtopics.

Return STRICT JSON, no prose, in this exact shape:

{
  "mappings": [
    {
      "planner_topic":    "<verbatim>",
      "planner_subtopic": "<verbatim>",
      "lms_topic_ids":    ["<id1>", "<id2>"],
      "confidence":       0.0..1.0,
      "reasoning":        "one short sentence"
    }
  ]
}

Confidence scoring:
  ≥ 0.9 — exact subject match (e.g. "Phyllodes tumor" ↔ LMS "Phyllodes tumour")
  0.7-0.89 — clear subject overlap
  0.5-0.69 — partial overlap (worth surfacing for admin review)
  < 0.5 — return [] for lms_topic_ids; do NOT guess

Never invent LMS topic ids. Only use ids that appear in the supplied list.
If nothing fits, return an empty `lms_topic_ids` array.
"""


def fetch_lms_hierarchy() -> List[Dict[str, Any]]:
    """Pull the full Cat → Sub → Topic tree using the admin token."""
    if not LMS_BASE or not LMS_ADMIN_TOKEN:
        raise SystemExit("LMS_BASE_URL and LMS_ADMIN_TOKEN must be set in env")

    with httpx.Client(base_url=LMS_BASE, timeout=30.0) as c:
        r = c.get("/api/planner/syllabus-bundle", headers={"authorization": LMS_ADMIN_TOKEN})
    if r.status_code != 200:
        raise SystemExit(f"LMS bundle http {r.status_code}: {r.text[:300]}")
    body = r.json()
    return (body.get("data") or body).get("categories", []) or []


def flatten_lms_topics(categories: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """[{id, name, path: 'Cat › Sub › Topic'}, ...]"""
    out = []
    for cat in categories:
        for sub in cat.get("subcategories", []) or []:
            for topic in sub.get("topics", []) or []:
                out.append({
                    "id": str(topic.get("_id") or topic.get("id")),
                    "name": topic.get("name", ""),
                    "path": f"{cat.get('name','?')} › {sub.get('name','?')} › {topic.get('name','?')}",
                })
    return out


def planner_subtopics() -> List[Dict[str, str]]:
    """Walk SYLLABUS_TREE → flat list of (topic, subtopic) pairs."""
    out: List[Dict[str, str]] = []
    for topic_name, payload in SYLLABUS_TREE.items():
        subs = payload if isinstance(payload, list) else payload.get("subtopics", [])
        for sub in subs:
            sub_name = sub if isinstance(sub, str) else sub.get("name", str(sub))
            out.append({"planner_topic": topic_name, "planner_subtopic": sub_name})
    return out


def chunked(lst: List[Any], size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def map_chunk(
    chunk: List[Dict[str, str]],
    lms_topics: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """One Sonnet call per chunk of planner subtopics."""
    user_prompt = json.dumps(
        {
            "planner_pairs": chunk,
            "lms_topics": lms_topics,
            "instructions": (
                "Map every planner_pair to zero or more LMS topic ids from the list. "
                "Return STRICT JSON only, matching the shape in the system prompt."
            ),
        },
        ensure_ascii=False,
    )

    resp = call_claude_json(
        prompt=user_prompt,
        system=SYSTEM_PROMPT,
        model=SONNET,
        max_tokens=8000,
    )
    parsed = resp.get("json") or {}
    if not isinstance(parsed, dict):
        log.warning("  Sonnet returned non-dict JSON: %r", parsed)
        return []
    return parsed.get("mappings", []) or []


def persist(mappings: List[Dict[str, Any]], dry_run: bool) -> int:
    if dry_run:
        for m in mappings:
            print(json.dumps(m, ensure_ascii=False))
        return len(mappings)

    db = SessionLocal()
    written = 0
    try:
        for m in mappings:
            pt = m.get("planner_topic")
            ps = m.get("planner_subtopic")
            if not pt or not ps:
                continue
            existing = (
                db.query(SyllabusMapping)
                .filter(SyllabusMapping.planner_topic == pt, SyllabusMapping.planner_subtopic == ps)
                .one_or_none()
            )
            # Never trample manual overrides.
            if existing and existing.source == "manual":
                continue
            if existing:
                existing.lms_topic_ids = m.get("lms_topic_ids", [])
                existing.confidence = m.get("confidence")
                existing.source = "claude"
                existing.updated_at = datetime.utcnow()
            else:
                db.add(SyllabusMapping(
                    planner_topic=pt,
                    planner_subtopic=ps,
                    lms_topic_ids=m.get("lms_topic_ids", []),
                    confidence=m.get("confidence"),
                    source="claude",
                ))
            written += 1
        db.commit()
    finally:
        db.close()
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print mappings, don't write")
    ap.add_argument("--topic", help="restrict to a single planner topic name")
    ap.add_argument("--chunk-size", type=int, default=25, help="planner subtopics per Claude call")
    args = ap.parse_args()

    log.info("Fetching LMS hierarchy…")
    cats = fetch_lms_hierarchy()
    lms_topics = flatten_lms_topics(cats)
    log.info("Loaded %d LMS topics from %d categories", len(lms_topics), len(cats))

    pairs = planner_subtopics()
    if args.topic:
        pairs = [p for p in pairs if p["planner_topic"].lower() == args.topic.lower()]
        log.info("Filtered to %d pairs under planner topic '%s'", len(pairs), args.topic)
    log.info("Mapping %d planner subtopics → LMS topics in chunks of %d", len(pairs), args.chunk_size)

    all_mappings: List[Dict[str, Any]] = []
    for i, chunk in enumerate(chunked(pairs, args.chunk_size), start=1):
        log.info("Chunk %d (%d pairs)…", i, len(chunk))
        try:
            mappings = map_chunk(chunk, lms_topics)
            log.info("  → %d mappings returned", len(mappings))
            all_mappings.extend(mappings)
        except Exception as e:
            log.exception("  chunk %d failed: %s", i, e)

    written = persist(all_mappings, dry_run=args.dry_run)
    log.info("Done. %s %d mappings.", "Would write" if args.dry_run else "Wrote", written)


if __name__ == "__main__":
    main()
