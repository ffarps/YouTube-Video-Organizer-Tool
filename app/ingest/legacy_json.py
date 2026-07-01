"""One-time migration of the legacy category-keyed videos.json into SQLite.

Legacy shape:
    {"Category": [{"title", "url", "watched", ...optional metadata}, ...]}

Fixes applied during migration: URLs are canonicalized to 11-char video ids
(youtu.be / watch?v= / shorts duplicates merge), categories become themes in
a many-to-many table, and the watched bool becomes a watch_state row.
"""
import json
import sqlite3
from typing import Optional

from app import db
from app.ingest import urls


def _published_at(upload_date: Optional[str]) -> Optional[str]:
    if upload_date and len(upload_date) == 8 and upload_date.isdigit():
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}T00:00:00Z"
    return None


def migrate_videos_json(conn: sqlite3.Connection, json_path: str) -> dict:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    added = 0
    merged = 0
    unparseable = []

    for category, entries in data.items():
        theme_id = db.get_or_create_theme(conn, category)
        for entry in entries:
            video_id = urls.video_id_from_url(entry.get("url", ""))
            if not video_id:
                unparseable.append(entry.get("url"))
                continue
            was_new = db.upsert_video(
                conn,
                {
                    "id": video_id,
                    "title": entry.get("title", ""),
                    "description": entry.get("description"),
                    "channel_title": entry.get("channel"),
                    "duration_sec": entry.get("duration"),
                    "published_at": _published_at(entry.get("upload_date")),
                    "thumbnail_url": entry.get("thumbnail"),
                    "view_count": entry.get("view_count"),
                    "source": "legacy",
                },
            )
            added += was_new
            merged += not was_new
            db.assign_theme(conn, video_id, theme_id, 1.0, "manual")
            if entry.get("watched"):
                db.set_watch_state(conn, video_id, status="watched")

    conn.commit()
    return {
        "videos_added": added,
        "cross_category_duplicates_merged": merged,
        "unparseable_urls": unparseable,
    }
