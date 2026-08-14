"""Sync orchestration: given any YouTube URL, pick the right ingester,
upsert into the database, and apply the rule-based theme layer."""
import sqlite3
from typing import Callable, List, Optional

from app import db
from app.categorize import rules
from app.ingest import urls, youtube_api, ytdlp


class SyncError(Exception):
    pass


def _store_videos(conn: sqlite3.Connection, videos: List[dict]) -> dict:
    added = 0
    updated = 0
    custom_rules = db.list_theme_rules(conn)
    overrides = db.builtin_theme_overrides(conn)
    for video in videos:
        if db.upsert_video(conn, video):
            added += 1
            for theme_name, confidence in rules.assign_themes(
                video, custom_rules, overrides
            ):
                theme_id = db.get_or_create_theme(conn, theme_name)
                db.assign_theme(conn, video["id"], theme_id, confidence, "rule")
        else:
            updated += 1
    return {"added": added, "updated": updated}


def _fetch_metadata(
    video_ids: List[str], api_key: Optional[str]
) -> List[dict]:
    if api_key:
        return youtube_api.fetch_videos_metadata(api_key, video_ids)
    return ytdlp.fetch_videos_full(video_ids)


def _fetch_in_chunks(
    new_ids: List[str],
    api_key: Optional[str],
    report: Callable[[dict], None],
) -> List[dict]:
    """Fetch metadata in chunks so progress is reportable. The Data API
    batches 50 ids per request anyway; yt-dlp fetches one video at a time,
    so use small chunks there for a smoother bar."""
    chunk_size = 50 if api_key else 5
    videos: List[dict] = []
    for start in range(0, len(new_ids), chunk_size):
        videos.extend(_fetch_metadata(new_ids[start : start + chunk_size], api_key))
        report({
            "stage": "fetching",
            "done": min(start + chunk_size, len(new_ids)),
            "total": len(new_ids),
        })
    return videos


def add_videos(
    conn: sqlite3.Connection,
    text: str,
    api_key: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Bulk-add: extract every video id from pasted free-form text, fetch
    metadata for the unknown ones, and store them."""
    report = progress or (lambda event: None)
    video_ids, invalid = urls.extract_video_ids(text)
    if not video_ids:
        detail = f" (first bad entry: {invalid[0]})" if invalid else ""
        raise SyncError(f"No recognizable YouTube video URLs in the text{detail}")

    known = db.existing_video_ids(conn, video_ids)
    new_ids = [vid for vid in video_ids if vid not in known]
    report({
        "stage": "plan",
        "total_in_source": len(video_ids),
        "new": len(new_ids),
        "known": len(known),
    })
    videos = _fetch_in_chunks(new_ids, api_key, report)
    report({"stage": "storing"})
    counts = _store_videos(conn, videos)
    conn.commit()
    return {
        "kind": "bulk",
        "requested": len(video_ids),
        "added": counts["added"],
        "already_known": len(known),
        "unavailable": len(new_ids) - len(videos),
        "invalid": invalid,
    }


def add_video(conn: sqlite3.Connection, url: str, api_key: Optional[str]) -> dict:
    video_id = urls.video_id_from_url(url)
    if not video_id:
        raise SyncError(f"Not a recognizable YouTube video URL: {url}")
    videos = _fetch_metadata([video_id], api_key)
    if not videos:
        raise SyncError(f"Video {video_id} not found (private or deleted?)")
    counts = _store_videos(conn, videos)
    conn.commit()
    return {**counts, "video": db.get_video(conn, video_id)}


def sync_source(
    conn: sqlite3.Connection,
    url: str,
    api_key: Optional[str] = None,
    cookies_browser: Optional[str] = None,
    progress: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Sync a playlist, channel, Watch Later, or single video URL.

    `progress` (optional) is called with {"stage": ...} events as the sync
    advances: listing -> plan (totals known) -> fetching (done/total) ->
    storing. Used by /sync/stream to drive the UI progress bar."""
    report = progress or (lambda event: None)
    ref = urls.classify_url(url)
    kind = ref["kind"]

    if kind == "unknown":
        raise SyncError(f"Could not recognize URL as video/playlist/channel: {url}")

    if kind == "video":
        report({"stage": "fetching", "done": 0, "total": 1})
        result = {"kind": "video", **add_video(conn, url, api_key)}
        report({"stage": "fetching", "done": 1, "total": 1})
        return result

    report({"stage": "listing"})

    if kind == "channel":
        if not api_key:
            raise SyncError(
                "Channel sync requires a YouTube Data API key "
                "(set YOUTUBE_API_KEY in .env)"
            )
        channel = youtube_api.resolve_channel_uploads_playlist(
            api_key,
            channel_id=ref.get("channel_id"),
            handle=ref.get("handle"),
        )
        if channel is None:
            raise SyncError(f"Channel not found: {url}")
        playlist_id = channel["uploads_playlist_id"]
        title = channel["title"]
        video_ids = youtube_api.fetch_playlist_video_ids(api_key, playlist_id)
    elif kind == "watch_later":
        if not cookies_browser:
            raise SyncError(
                "Watch Later requires browser cookies "
                "(set YTDLP_COOKIES_BROWSER=firefox or chrome in .env)"
            )
        listing = ytdlp.list_playlist(
            "https://www.youtube.com/playlist?list=WL", cookies_browser
        )
        playlist_id = "WL"
        title = listing.get("title") or "Watch Later"
        video_ids = listing["video_ids"]
    else:  # public/unlisted playlist
        playlist_id = ref["playlist_id"]
        if api_key:
            title = youtube_api.fetch_playlist_title(api_key, playlist_id)
            video_ids = youtube_api.fetch_playlist_video_ids(api_key, playlist_id)
        else:
            listing = ytdlp.list_playlist(
                f"https://www.youtube.com/playlist?list={playlist_id}",
                cookies_browser,
            )
            title = listing.get("title")
            video_ids = listing["video_ids"]

    # Only fetch metadata for videos we don't already have; known ones are
    # just re-linked to the playlist. Keeps re-syncs cheap and idempotent.
    known = db.existing_video_ids(conn, video_ids)
    new_ids = [vid for vid in video_ids if vid not in known]
    report({
        "stage": "plan",
        "total_in_source": len(video_ids),
        "new": len(new_ids),
        "known": len(known),
    })

    videos = _fetch_in_chunks(new_ids, api_key, report)
    report({"stage": "storing"})
    counts = _store_videos(conn, videos)

    kind_label = "watch_later" if playlist_id == "WL" else kind
    db.upsert_playlist(conn, playlist_id, title, kind_label)
    for position, vid in enumerate(video_ids):
        if vid in known or any(v["id"] == vid for v in videos):
            db.upsert_playlist_item(conn, playlist_id, vid, position)
    conn.commit()

    return {
        "kind": kind,
        "playlist_id": playlist_id,
        "title": title,
        "total_in_source": len(video_ids),
        "added": counts["added"],
        "already_known": len(known),
        # listed in the source but not fetchable (private/deleted)
        "unavailable": len(new_ids) - len(videos),
    }
