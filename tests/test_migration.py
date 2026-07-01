import json

from app import db
from app.ingest.legacy_json import migrate_videos_json

LEGACY = {
    "Guitar": [
        {
            "title": "Learn guitar in 30 days",
            "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            "watched": True,
            "duration": 600,
            "channel": "GuitarChannel",
            "upload_date": "20240115",
        }
    ],
    "Tech": [
        # same video as Guitar's but via youtu.be — must merge, not duplicate
        {
            "title": "Learn guitar in 30 days",
            "url": "https://youtu.be/aaaaaaaaaaa",
            "watched": False,
        },
        {
            "title": "Building a NAS",
            "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
            "watched": False,
        },
        {"title": "Broken entry", "url": "https://example.com/nope", "watched": False},
    ],
}


def test_migration(tmp_path):
    json_path = tmp_path / "videos.json"
    json_path.write_text(json.dumps(LEGACY), encoding="utf-8")
    conn = db.connect(str(tmp_path / "test.db"))
    db.init_db(conn)

    result = migrate_videos_json(conn, str(json_path))

    assert result["videos_added"] == 2
    assert result["cross_category_duplicates_merged"] == 1
    assert result["unparseable_urls"] == ["https://example.com/nope"]

    # the duplicate carries both themes now
    video = db.get_video(conn, "aaaaaaaaaaa")
    assert sorted(video["themes"]) == ["Guitar", "Tech"]
    assert video["watch_status"] == "watched"
    assert video["duration_sec"] == 600
    assert video["published_at"] == "2024-01-15T00:00:00Z"

    assert db.get_video(conn, "bbbbbbbbbbb")["watch_status"] == "unwatched"

    themes = {t["name"]: t["video_count"] for t in db.list_themes(conn)}
    assert themes == {"Guitar": 1, "Tech": 2}
