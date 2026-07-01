from typing import List, Literal, Optional

from pydantic import BaseModel, Field

WatchStatus = Literal["unwatched", "watched", "skipped"]


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


class SyncRequest(BaseModel):
    url: str


class AddVideoRequest(BaseModel):
    url: str
    themes: List[str] = []


class WatchStateUpdate(BaseModel):
    status: Optional[WatchStatus] = None
    rating: Optional[int] = Field(default=None, ge=1, le=5)


class ThemeAssignRequest(BaseModel):
    name: str


class ThemeCreateRequest(BaseModel):
    name: str
    video_ids: List[str] = []


class AutoAssignRequest(BaseModel):
    threshold: float = Field(default=0.45, ge=0.0, le=1.0)


class DiscoverRequest(BaseModel):
    min_cluster_size: int = Field(default=5, ge=2)
    scope: Literal["unthemed", "all"] = "unthemed"
