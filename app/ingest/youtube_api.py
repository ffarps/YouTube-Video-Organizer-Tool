"""YouTube Data API v3 ingestion.

Quota math (10,000 free units/day): playlistItems.list and videos.list each
cost 1 unit and return up to 50 items, so a 500-video playlist costs ~20 units.
search.list (100 units) is deliberately never used.
"""
import re
from typing import List, Optional

from googleapiclient.discovery import build

ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T?(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?$"
)


def parse_iso8601_duration(value: Optional[str]) -> Optional[int]:
    """Convert an ISO 8601 duration like PT1H2M3S to seconds."""
    if not value:
        return None
    match = ISO_DURATION_RE.match(value)
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    return (
        parts.get("days", 0) * 86400
        + parts.get("h", 0) * 3600
        + parts.get("m", 0) * 60
        + parts.get("s", 0)
    )


def _client(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def fetch_playlist_video_ids(api_key: str, playlist_id: str) -> List[str]:
    """Page through playlistItems.list; 1 quota unit per 50 videos."""
    youtube = _client(api_key)
    ids: List[str] = []
    page_token = None
    while True:
        response = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        ids += [item["contentDetails"]["videoId"] for item in response.get("items", [])]
        page_token = response.get("nextPageToken")
        if not page_token:
            return ids


def fetch_playlist_title(api_key: str, playlist_id: str) -> Optional[str]:
    youtube = _client(api_key)
    response = (
        youtube.playlists().list(part="snippet", id=playlist_id, maxResults=1).execute()
    )
    items = response.get("items", [])
    return items[0]["snippet"]["title"] if items else None


def fetch_videos_metadata(api_key: str, video_ids: List[str]) -> List[dict]:
    """Batch videos.list in chunks of 50 ids (1 quota unit per chunk)."""
    youtube = _client(api_key)
    videos: List[dict] = []
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start : start + 50]
        response = (
            youtube.videos()
            .list(part="snippet,contentDetails,statistics", id=",".join(chunk))
            .execute()
        )
        for item in response.get("items", []):
            snippet = item["snippet"]
            thumbnails = snippet.get("thumbnails", {})
            thumb = (
                thumbnails.get("medium") or thumbnails.get("default") or {}
            ).get("url")
            stats = item.get("statistics", {})
            videos.append(
                {
                    "id": item["id"],
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description"),
                    "channel_id": snippet.get("channelId"),
                    "channel_title": snippet.get("channelTitle"),
                    "duration_sec": parse_iso8601_duration(
                        item.get("contentDetails", {}).get("duration")
                    ),
                    "published_at": snippet.get("publishedAt"),
                    "thumbnail_url": thumb,
                    "tags": snippet.get("tags", []),
                    "view_count": int(stats["viewCount"])
                    if "viewCount" in stats
                    else None,
                    "source": "api",
                }
            )
    return videos


def resolve_channel_uploads_playlist(
    api_key: str, channel_id: Optional[str] = None, handle: Optional[str] = None
) -> Optional[dict]:
    """Resolve a channel to its uploads playlist id (1 quota unit)."""
    youtube = _client(api_key)
    kwargs = {"part": "contentDetails,snippet", "maxResults": 1}
    if channel_id:
        kwargs["id"] = channel_id
    elif handle:
        kwargs["forHandle"] = handle
    else:
        return None
    response = youtube.channels().list(**kwargs).execute()
    items = response.get("items", [])
    if not items:
        return None
    return {
        "uploads_playlist_id": items[0]["contentDetails"]["relatedPlaylists"]["uploads"],
        "title": items[0]["snippet"]["title"],
    }
