"""yt-dlp ingestion: keyless fallback and the only way into Watch Later.

The YouTube Data API cannot read the Watch Later playlist (blocked since
2016). yt-dlp with browser cookies can, so private lists are flat-listed here
(fast, ids only) and then enriched through the Data API's videos.list batch
path when an API key is configured.
"""
from typing import List, Optional

import yt_dlp


def _flat_opts(cookies_browser: Optional[str]) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def list_playlist(url: str, cookies_browser: Optional[str] = None) -> dict:
    """Flat-extract a playlist: returns {'title': ..., 'video_ids': [...]}.

    Fast — one request per page of the playlist, no per-video fetches.
    """
    with yt_dlp.YoutubeDL(_flat_opts(cookies_browser)) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") or []
    return {
        "title": info.get("title"),
        "video_ids": [e["id"] for e in entries if e and e.get("id")],
    }


def fetch_video_full(url_or_id: str) -> dict:
    """Full single-video extraction (slow: one page fetch). Keyless fallback."""
    with yt_dlp.YoutubeDL(
        {"quiet": True, "no_warnings": True, "skip_download": True}
    ) as ydl:
        info = ydl.extract_info(url_or_id, download=False)
    return _map_info(info)


def fetch_videos_full(video_ids: List[str]) -> List[dict]:
    """Full extraction for many ids — slow (one fetch per video); used only
    when no Data API key is configured.

    Public playlists can contain private/deleted/region-blocked entries;
    those are skipped instead of failing the whole sync (the Data API path
    behaves the same way — videos.list silently omits them)."""
    videos = []
    for vid in video_ids:
        try:
            videos.append(fetch_video_full(vid))
        except yt_dlp.utils.DownloadError:
            continue
    return videos


def _map_info(info: dict) -> dict:
    upload_date = info.get("upload_date")  # YYYYMMDD
    published_at = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}T00:00:00Z"
        if upload_date and len(upload_date) == 8
        else None
    )
    return {
        "id": info["id"],
        "title": info.get("title", ""),
        "description": info.get("description"),
        "channel_id": info.get("channel_id"),
        "channel_title": info.get("channel") or info.get("uploader"),
        "duration_sec": int(info["duration"]) if info.get("duration") else None,
        "published_at": published_at,
        "thumbnail_url": info.get("thumbnail"),
        "tags": info.get("tags") or [],
        "view_count": info.get("view_count"),
        "source": "ytdlp",
    }
