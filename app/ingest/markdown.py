"""Markdown importer — fallback for lists that can't be reached via API.

Format (same as the legacy md_to_json.py):
    # Category Name
    - [Video Title](https://www.youtube.com/watch?v=...)
"""
import re
import sqlite3
from typing import Dict, List

from app import db
from app.ingest import urls

CATEGORY_RE = re.compile(r"^#\s*(?:Category:\s*)?(.+)$")
VIDEO_RE = re.compile(
    r"^\s*-\s*\[(.*?)\]\((https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s)]+)\)"
)


def parse_markdown(text: str) -> Dict[str, List[dict]]:
    """Parse a markdown list into {category: [{'title', 'url', 'video_id'}]}."""
    result: Dict[str, List[dict]] = {}
    current: str | None = None
    for line in text.splitlines():
        category_match = CATEGORY_RE.match(line)
        if category_match:
            current = category_match.group(1).strip()
            result.setdefault(current, [])
            continue
        video_match = VIDEO_RE.match(line)
        if video_match and current is not None:
            title, url = video_match.group(1).strip(), video_match.group(2).strip()
            video_id = urls.video_id_from_url(url)
            if video_id:
                result[current].append(
                    {"title": title, "url": url, "video_id": video_id}
                )
    return result


def import_markdown(conn: sqlite3.Connection, text: str) -> dict:
    """Import a markdown list: categories become themes, links become videos
    (title-only metadata; enrich later via sync)."""
    parsed = parse_markdown(text)
    added = 0
    skipped = 0
    for category, entries in parsed.items():
        theme_id = db.get_or_create_theme(conn, category)
        for entry in entries:
            was_new = db.upsert_video(
                conn,
                {
                    "id": entry["video_id"],
                    "title": entry["title"],
                    "source": "markdown",
                },
            )
            db.assign_theme(conn, entry["video_id"], theme_id, 1.0, "manual")
            added += was_new
            skipped += not was_new
    conn.commit()
    return {"added": added, "already_known": skipped}
