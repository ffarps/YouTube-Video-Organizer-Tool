"""Build demo.db: a small library for screenshots, never a real one.

Every video is freely licensed or public domain — Blender Studio's open films
(CC BY), NASA (public domain) and MIT OpenCourseWare (CC BY-NC-SA) — so the
README can show real thumbnails without showing anyone's own watch list.
Metadata is written in by hand, so this runs offline and gives the same
library every time.

    python scripts/make_demo_db.py            # writes demo.db
    DATABASE_PATH=demo.db python -m uvicorn app.main:app --port 8766
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "demo.db"

# (id, title, channel, seconds, theme)
VIDEOS = [
    ("aqz-KE-bpKQ", "Big Buck Bunny", "Blender", 635, "Animation"),
    ("1C_zuHf6lP4", "Highlights: First Images from the James Webb Space Telescope", "NASA", 201, "Space"),
    ("nykOeWgQcHM", "1. What is Computation?", "MIT OpenCourseWare", 2586, "Computer Science"),
    ("eRsGyueVLvQ", "Sintel - Open Movie by Blender Foundation", "Blender", 888, "Animation"),
    ("J7DzL2_Na80", "1. The Geometry of Linear Equations", "MIT OpenCourseWare", 2389, "Maths"),
    ("WhWc3b3KhnY", "Spring - Blender Open Movie", "Blender Studio", 464, "Animation"),
    ("mvPH_gDMarw", "Highlights From SDO's 10 Years of Solar Observation", "NASA Goddard", 313, "Space"),
    ("ZA-tUyM_y7s", "1. Algorithms and Computation", "MIT OpenCourseWare", 2738, "Computer Science"),
    ("_cMxraX_5RE", "Sprite Fright - Blender Open Movie", "Blender Studio", 630, "Animation"),
    ("L6dx0pO5MSw", "NASA's Perseverance Rover Lands Successfully on Mars", "NASA Jet Propulsion Laboratory", 65, "Space"),
    ("R6MlUcmOul8", "Tears of Steel - Blender VFX Open Movie", "Blender", 734, "Animation"),
    ("7K1sB05pE0A", "Lec 1 | MIT 18.01 Single Variable Calculus", "MIT OpenCourseWare", 3092, "Maths"),
    ("UXqq0ZvbOnk", "CHARGE - Blender Open Movie", "Blender Studio", 263, "Animation"),
    ("HBtdbaSKexU", "ScienceCasts: The Power of Light", "NASA Science", 255, "Space"),
    ("STjW3eH0Cik", "6. Search: Games, Minimax, and Alpha-Beta", "MIT OpenCourseWare", 2896, "Computer Science"),
    ("PVGeM40dABA", "Coffee Run - Blender Open Movie", "Blender Studio", 185, "Animation"),
]

MODES = {"Animation": "leisure", "Space": "study", "Computer Science": "study", "Maths": "study"}

# a little history, so the cards show what a used library looks like
WATCHED = {"aqz-KE-bpKQ": 1, "1C_zuHf6lP4": 1, "PVGeM40dABA": None}
RESUME = {"nykOeWgQcHM": 1260.0, "eRsGyueVLvQ": 410.0, "WhWc3b3KhnY": 95.0}
PLAYS = {"aqz-KE-bpKQ": 3, "1C_zuHf6lP4": 2, "PVGeM40dABA": 1, "nykOeWgQcHM": 1, "eRsGyueVLvQ": 1}
TREADMILL = ["WhWc3b3KhnY", "_cMxraX_5RE", "UXqq0ZvbOnk", "R6MlUcmOul8"]


def main() -> None:
    OUT.unlink(missing_ok=True)
    conn = db.connect(str(OUT))
    db.init_db(conn)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for n, (vid, title, channel, seconds, theme) in enumerate(VIDEOS):
        db.upsert_video(conn, {
            "id": vid, "title": title, "channel_title": channel,
            "duration_sec": seconds,
            "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            # listed order = newest first under "recently added"
            "added_at": (start - timedelta(hours=n)).isoformat(timespec="seconds"),
        })
        db.assign_theme(conn, vid, db.get_or_create_theme(conn, theme), 1.0, "manual")
    for theme, mode in MODES.items():
        db.set_theme_mode(conn, theme, mode)
    for vid, rating in WATCHED.items():
        db.set_watch_state(conn, vid, "watched", rating)
    for vid, seconds in RESUME.items():
        db.set_resume_position(conn, vid, seconds)
    for vid, times in PLAYS.items():
        for _ in range(times):
            db.record_play(conn, vid)
    playlist = db.create_playlist(conn, "Treadmill")
    for vid in TREADMILL:
        db.add_playlist_video(conn, playlist["id"], vid)
    conn.commit()
    conn.close()
    print(f"wrote {OUT} ({len(VIDEOS)} videos)")


if __name__ == "__main__":
    main()
