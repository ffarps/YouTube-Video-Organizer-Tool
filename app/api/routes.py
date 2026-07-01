import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.categorize import themes as theming
from app.categorize.embeddings import EmbeddingUnavailable
from app.config import Settings, get_settings
from app.ingest import sync
from app.models import (
    AddVideoRequest,
    AutoAssignRequest,
    DiscoverRequest,
    SyncRequest,
    ThemeAssignRequest,
    ThemeCreateRequest,
    WatchStateUpdate,
)
from app.recommend import engine

router = APIRouter()


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


@router.post("/sync")
def sync_source(
    body: SyncRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Sync a playlist, channel, Watch Later, or single video URL."""
    try:
        return sync.sync_source(
            conn,
            body.url,
            api_key=settings.youtube_api_key,
            cookies_browser=settings.ytdlp_cookies_browser,
        )
    except sync.SyncError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/videos", status_code=201)
def add_video(
    body: AddVideoRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        result = sync.add_video(conn, body.url, settings.youtube_api_key)
    except sync.SyncError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for theme_name in body.themes:
        theme_id = db.get_or_create_theme(conn, theme_name)
        db.assign_theme(conn, result["video"]["id"], theme_id, 1.0, "manual")
    conn.commit()
    return db.get_video(conn, result["video"]["id"])


@router.get("/themes")
def list_themes(conn: sqlite3.Connection = Depends(get_db)):
    return {"themes": db.list_themes(conn)}


@router.get("/themes/{theme_name}/videos")
def videos_by_theme(
    theme_name: str,
    watched: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    videos = db.videos_by_theme(conn, theme_name, watched, limit, offset)
    if videos is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    return {"theme": theme_name, "videos": videos}


@router.get("/videos/{video_id}")
def get_video(video_id: str, conn: sqlite3.Connection = Depends(get_db)):
    video = db.get_video(conn, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@router.delete("/videos/{video_id}")
def delete_video(video_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_video(conn, video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    conn.commit()
    return {"message": "Video deleted"}


# --- theming (Phase 2) -----------------------------------------------------

@router.post("/embeddings/build")
def build_embeddings(limit: int = 500, conn: sqlite3.Connection = Depends(get_db)):
    """Embed videos that don't have a vector yet (requires the [ml] extra)."""
    try:
        return theming.build_embeddings(conn, limit)
    except EmbeddingUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/themes/auto-assign")
def auto_assign_themes(
    body: AutoAssignRequest, conn: sqlite3.Connection = Depends(get_db)
):
    return theming.auto_assign(conn, threshold=body.threshold)


@router.post("/themes/discover")
def discover_themes(body: DiscoverRequest, conn: sqlite3.Connection = Depends(get_db)):
    try:
        return theming.discover(conn, body.min_cluster_size, body.scope)
    except EmbeddingUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/themes", status_code=201)
def create_theme(body: ThemeCreateRequest, conn: sqlite3.Connection = Depends(get_db)):
    """Create a theme (e.g. confirming a discovered cluster) and optionally
    assign videos to it."""
    theme_id = db.get_or_create_theme(conn, body.name)
    for video_id in body.video_ids:
        db.assign_theme(conn, video_id, theme_id, 1.0, "manual")
    conn.commit()
    return {"name": body.name, "assigned": len(body.video_ids)}


@router.get("/review")
def review_queue(limit: int = 50, conn: sqlite3.Connection = Depends(get_db)):
    """Unthemed videos with ranked theme suggestions."""
    return {"queue": theming.review_queue(conn, limit)}


@router.post("/videos/{video_id}/themes")
def assign_video_theme(
    video_id: str,
    body: ThemeAssignRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    if db.get_video(conn, video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")
    theme_id = db.get_or_create_theme(conn, body.name)
    db.assign_theme(conn, video_id, theme_id, 1.0, "manual")
    conn.commit()
    return db.get_video(conn, video_id)


@router.delete("/videos/{video_id}/themes/{theme_name}")
def remove_video_theme(
    video_id: str, theme_name: str, conn: sqlite3.Connection = Depends(get_db)
):
    if not db.remove_theme_assignment(conn, video_id, theme_name):
        raise HTTPException(status_code=404, detail="Assignment not found")
    conn.commit()
    return db.get_video(conn, video_id)


# --- recommendations (Phase 3) ----------------------------------------------

@router.get("/recommendations")
def recommendations(
    theme: Optional[str] = None,
    max_duration: Optional[int] = None,  # seconds
    limit: int = 20,
    conn: sqlite3.Connection = Depends(get_db),
):
    return engine.recommend(conn, theme, max_duration, limit)


@router.patch("/videos/{video_id}/watch-state")
def update_watch_state(
    video_id: str,
    body: WatchStateUpdate,
    conn: sqlite3.Connection = Depends(get_db),
):
    if body.status is None and body.rating is None:
        raise HTTPException(status_code=400, detail="Provide status and/or rating")
    if not db.set_watch_state(conn, video_id, body.status, body.rating):
        raise HTTPException(status_code=404, detail="Video not found")
    conn.commit()
    return db.get_video(conn, video_id)
