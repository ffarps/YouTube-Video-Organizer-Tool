"""Migrate a legacy videos.json into the SQLite database.

Usage: python scripts/migrate_videos_json.py [videos.json] [organizer.db]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.ingest.legacy_json import migrate_videos_json  # noqa: E402


def main() -> None:
    json_path = sys.argv[1] if len(sys.argv) > 1 else "videos.json"
    db_path = sys.argv[2] if len(sys.argv) > 2 else "organizer.db"
    if not Path(json_path).is_file():
        print(f"Error: {json_path} not found")
        sys.exit(1)
    conn = db.connect(db_path)
    db.init_db(conn)
    result = migrate_videos_json(conn, json_path)
    conn.close()
    print(f"Migrated into {db_path}:")
    print(f"  videos added:              {result['videos_added']}")
    print(f"  cross-category dupes merged: {result['cross_category_duplicates_merged']}")
    if result["unparseable_urls"]:
        print(f"  unparseable URLs ({len(result['unparseable_urls'])}):")
        for url in result["unparseable_urls"]:
            print(f"    {url}")


if __name__ == "__main__":
    main()
