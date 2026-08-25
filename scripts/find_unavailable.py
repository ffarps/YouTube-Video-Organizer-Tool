"""List the videos in the library that YouTube will no longer serve.

A deleted or private video is simply absent from the `videos.list` response,
so one pass over the library is the whole check -- 50 ids per call, 1 quota
unit each, about 50 units for a few thousand videos. Needs YOUTUBE_API_KEY.

This is a script rather than a feature because of how rarely it finds
anything: a 2,400-video library had three. Reporting is the default; deleting
goes through the same path the API does, media file first so nothing is left
orphaned on disk.

Usage: python scripts/find_unavailable.py [--delete] [organizer.db]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, downloads  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.ingest.youtube_api import fetch_videos_metadata  # noqa: E402

# A whole library coming back "gone" means the answer is wrong, not that the
# library died: a key that lost its quota or an API change would look exactly
# like that, and --delete would act on it. Report, and refuse to delete.
SUSPICIOUS_SHARE = 0.1


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    delete = "--delete" in sys.argv[1:]
    settings = get_settings()
    if not settings.youtube_api_key:
        print("Error: YOUTUBE_API_KEY is not set (see .env.example)")
        sys.exit(1)
    db_path = args[0] if args else settings.database_path

    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT id, title, channel_title FROM videos ORDER BY channel_title, title"
    ).fetchall()
    if not rows:
        print(f"{db_path} holds no videos")
        return
    ids = [r["id"] for r in rows]
    print(f"checking {len(ids)} videos ({(len(ids) + 49) // 50} calls, "
          f"{(len(ids) + 49) // 50} quota units)...")
    alive = {v["id"] for v in fetch_videos_metadata(settings.youtube_api_key, ids)}
    gone = [r for r in rows if r["id"] not in alive]

    if not gone:
        print("every video in the library is still up")
        return
    print(f"\n{len(gone)} unavailable (deleted, private, or removed):")
    for row in gone:
        print(f"  https://youtu.be/{row['id']}  {str(row['channel_title'] or '-')[:24]:24}"
              f"  {row['title']}")

    if not delete:
        print("\nre-run with --delete to remove them from the library")
        return
    if len(gone) > len(rows) * SUSPICIOUS_SHARE:
        print(f"\nRefusing to delete: {len(gone)} of {len(rows)} came back missing,"
              " which looks like a bad answer rather than a dead library.")
        sys.exit(1)
    doomed = [r["id"] for r in gone]
    removed = downloads.remove_files_for(conn, doomed, settings.media_dir())
    for video_id in doomed:
        db.delete_video(conn, video_id)
    conn.commit()
    print(f"\ndeleted {len(doomed)} video(s)"
          + (f", and {len(removed)} downloaded file(s)" if removed else ""))


if __name__ == "__main__":
    main()
