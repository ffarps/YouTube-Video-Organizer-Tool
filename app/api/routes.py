import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app import db
from app.config import Settings, get_settings
from app.ingest import sync
from app.models import AddVideoRequest, SyncRequest, WatchStateUpdate

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
