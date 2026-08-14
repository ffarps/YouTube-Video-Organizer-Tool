from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

WatchStatus = Literal["unwatched", "watched", "skipped"]

DownloadStatus = Literal["queued", "downloading", "done", "error"]


class Video(BaseModel):
    id: str  # 11-char YouTube video id — the canonical identity
    title: str
    description: Optional[str] = None
    channel_id: Optional[str] = None
    channel_title: Optional[str] = None
    duration_sec: Optional[int] = None
    published_at: Optional[str] = None  # ISO 8601
    thumbnail_url: Optional[str] = None
    tags: List[str] = []
    view_count: Optional[int] = None
    source: Literal["api", "ytdlp", "markdown", "legacy"] = "api"


class VideoOut(Video):
    themes: List[str] = []
    watch_status: WatchStatus = "unwatched"
    rating: Optional[int] = None
    # Local copy, if one was downloaded: status drives the card badge and
    # download_file is the basename to play from /media.
    download_status: Optional[DownloadStatus] = None
    download_file: Optional[str] = None
    # How often you've come back to it — the signal a favourite shows up in.
    play_count: int = 0
    last_played_at: Optional[str] = None


class SyncRequest(BaseModel):
    url: str


class AddVideoRequest(BaseModel):
    url: str
    themes: List[str] = []


class BulkAddRequest(BaseModel):
    text: NonEmptyStr  # free-form pasted list of video URLs/ids


class WatchStateUpdate(BaseModel):
    status: Optional[WatchStatus] = None
    # Thumbs, not stars: -1 down, +1 up, 0 clears the vote back to "it was
    # okay". None means "don't touch the vote" (e.g. a status-only update).
    rating: Optional[int] = Field(default=None, ge=-1, le=1)


class ThemeAssignRequest(BaseModel):
    name: NonEmptyStr  # stripped — "mental health " and "mental health" are one theme


class ThemeCreateRequest(BaseModel):
    name: NonEmptyStr
    video_ids: List[str] = []


class BulkThemeRequest(BaseModel):
    video_ids: List[str] = Field(min_length=1)  # assign one theme to many videos
    name: NonEmptyStr


class BulkDeleteRequest(BaseModel):
    video_ids: List[str] = Field(min_length=1)  # remove many videos at once


class PlaylistCreateRequest(BaseModel):
    title: NonEmptyStr


class PlaylistVideoRequest(BaseModel):
    video_id: NonEmptyStr


class RuleCreateRequest(BaseModel):
    pattern: NonEmptyStr  # literal expression, matched on word boundaries
    theme: NonEmptyStr
    exclusive: bool = False


class AutoAssignRequest(BaseModel):
    threshold: float = Field(default=0.45, ge=0.0, le=1.0)


class DiscoverRequest(BaseModel):
    min_cluster_size: int = Field(default=5, ge=2)
    scope: Literal["unthemed", "all"] = "unthemed"


class RevealRequest(BaseModel):
    # None opens the media folder itself; an id selects that video's file.
    video_id: Optional[str] = None


class DownloadRequest(BaseModel):
    # None means "use DOWNLOAD_MAX_HEIGHT from .env". A taller value than
    # YouTube offers is fine — you just get the tallest that exists.
    max_height: Optional[int] = Field(default=None, ge=144, le=4320)
    # Audio only skips ffmpeg entirely: the m4a stream is a single file.
    audio_only: bool = False
    # Take whatever this particular video has, however tall. Overrides
    # max_height, because "best" is not a number the caller can know upfront.
    best: bool = False
