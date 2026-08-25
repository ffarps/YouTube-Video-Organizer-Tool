import json
import queue
import sqlite3
import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from app import db, downloads, logs
from app.categorize import rules
from app.categorize import themes as theming
from app.categorize.embeddings import EmbeddingUnavailable
from app.config import Settings, get_settings
from app.ingest import download as ytdl
from app.ingest import sync
from app.models import (
    AddVideoRequest,
    AutoAssignRequest,
    BulkAddRequest,
    BulkDeleteRequest,
    BulkThemeRequest,
    DiscoverRequest,
    DownloadRequest,
    PlaylistCreateRequest,
    PlaylistVideoRequest,
    RevealRequest,
    RuleCreateRequest,
    SyncRequest,
    ThemeAssignRequest,
    ThemeCreateRequest,
    WatchStateUpdate,
)
from app.recommend import engine

router = APIRouter()


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


@router.get("/diagnostics/stacks", response_class=PlainTextResponse)
def diagnostic_stacks():
    """What every thread is doing right now, also written to the log.

    Meant to be called from outside the app while the window is frozen: the
    server runs on its own thread, so it usually answers even when the UI has
    stopped. A useful reply means the freeze is in the window; no reply at all
    is an answer too.
    """
    return logs.dump_stacks("requested via /diagnostics/stacks")


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


def _ndjson_stream(work) -> StreamingResponse:
    """Run `work(progress_callback)` in a thread, streaming ND-JSON
    {"progress": ...} events as it advances, then a final {"result": ...}
    or {"error": ...} line."""

    def event_stream():
        events: "queue.Queue[tuple]" = queue.Queue()

        def run():
            try:
                events.put(("result", work(lambda e: events.put(("progress", e)))))
            except sync.SyncError as e:
                events.put(("error", str(e)))
            except Exception as e:  # surface unexpected failures to the client
                events.put(("error", f"{type(e).__name__}: {e}"))

        threading.Thread(target=run, daemon=True).start()
        while True:
            kind, payload = events.get()
            yield json.dumps({kind: payload}) + "\n"
            if kind in ("result", "error"):
                break

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/sync/stream")
def sync_source_stream(
    body: SyncRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Like /sync, but streams progress as ND-JSON."""
    return _ndjson_stream(
        lambda progress: sync.sync_source(
            conn,
            body.url,
            api_key=settings.youtube_api_key,
            cookies_browser=settings.ytdlp_cookies_browser,
            progress=progress,
        )
    )


@router.post("/videos/bulk/stream")
def add_videos_stream(
    body: BulkAddRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Bulk-add videos from pasted free-form text (any separators, markdown
    links welcome); streams progress as ND-JSON."""
    return _ndjson_stream(
        lambda progress: sync.add_videos(
            conn, body.text, settings.youtube_api_key, progress
        )
    )


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


@router.get("/videos")
def list_videos(
    search: Optional[str] = None,
    sort: str = "newest",
    watched: Optional[bool] = None,
    unthemed: Optional[bool] = None,
    downloaded: Optional[bool] = None,
    channel: Optional[str] = None,
    channel_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Browse/search the whole library, across themes.

    `channel`/`channel_id` narrow to one uploader — what the library already
    holds, not what the channel has on YouTube."""
    videos = db.list_videos(
        conn, search, sort, watched, unthemed, downloaded,
        channel=channel, channel_id=channel_id, limit=limit, offset=offset,
    )
    theme_map = db.themes_for_videos(conn, [v["id"] for v in videos])
    for video in videos:
        video["themes"] = theme_map.get(video["id"], [])
    return {"videos": videos}


@router.get("/themes")
def list_themes(
    watched: Optional[bool] = None, conn: sqlite3.Connection = Depends(get_db)
):
    """List themes with counts. Pass ``watched=false`` to count only each
    theme's unwatched videos, matching the Browse "unwatched only" filter."""
    return {
        "themes": db.list_themes(conn, watched),
        "total_videos": db.count_videos(conn, watched),
    }


@router.patch("/themes/{theme_name}")
def rename_theme(
    theme_name: str,
    body: ThemeAssignRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    """Rename a theme; renaming onto an existing theme merges into it."""
    result = db.rename_theme(conn, theme_name, body.name, rules.THEME_KEYWORDS)
    if result is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    conn.commit()
    return {"result": result, "name": body.name}


@router.delete("/themes/{theme_name}")
def delete_theme(theme_name: str, conn: sqlite3.Connection = Depends(get_db)):
    """Delete a theme and all its video assignments (videos stay)."""
    if not db.delete_theme(conn, theme_name):
        raise HTTPException(status_code=404, detail="Theme not found")
    conn.commit()
    return {"message": "Theme deleted"}


@router.get("/themes/{theme_name}/videos")
def videos_by_theme(
    theme_name: str,
    watched: Optional[bool] = None,
    sort: str = "newest",
    downloaded: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
):
    videos = db.videos_by_theme(
        conn, theme_name, watched, sort=sort, downloaded=downloaded,
        limit=limit, offset=offset,
    )
    if videos is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    theme_map = db.themes_for_videos(conn, [v["id"] for v in videos])
    for video in videos:
        video["themes"] = theme_map.get(video["id"], [])
    return {"theme": theme_name, "videos": videos}


@router.get("/videos/{video_id}")
def get_video(video_id: str, conn: sqlite3.Connection = Depends(get_db)):
    video = db.get_video(conn, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@router.delete("/videos/{video_id}")
def delete_video(
    video_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    # Unlink the media first: the cascade removes the `downloads` row, and a
    # forgotten row means a file nothing can ever find again.
    downloads.remove_files_for(conn, [video_id], settings.media_dir())
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


@router.post("/videos/themes/bulk")
def bulk_assign_theme(
    body: BulkThemeRequest, conn: sqlite3.Connection = Depends(get_db)
):
    """Assign one theme (manual) to many videos at once. Unknown video ids are
    skipped rather than failing the whole batch."""
    theme_id = db.get_or_create_theme(conn, body.name)
    known = db.existing_video_ids(conn, body.video_ids)
    for video_id in known:
        db.assign_theme(conn, video_id, theme_id, 1.0, "manual")
    conn.commit()
    return {"name": body.name, "assigned": len(known)}


@router.post("/videos/bulk/delete")
def bulk_delete_videos(
    body: BulkDeleteRequest,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Delete many videos from the library at once."""
    files = downloads.remove_files_for(conn, body.video_ids, settings.media_dir())
    deleted = db.delete_videos(conn, body.video_ids)
    conn.commit()
    return {"deleted": deleted, "media_removed": files}


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


# --- playlists ---------------------------------------------------------------

def _editable_playlist(conn: sqlite3.Connection, playlist_id: str) -> dict:
    playlist = db.get_playlist(conn, playlist_id)
    if playlist is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if playlist["kind"] != "local":
        raise HTTPException(
            status_code=400,
            detail="Only local playlists can be edited (synced ones are overwritten on re-sync)",
        )
    return playlist


@router.get("/playlists")
def list_playlists(conn: sqlite3.Connection = Depends(get_db)):
    return {"playlists": db.list_playlists(conn)}


@router.post("/playlists", status_code=201)
def create_playlist(
    body: PlaylistCreateRequest, conn: sqlite3.Connection = Depends(get_db)
):
    playlist = db.create_playlist(conn, body.title)
    conn.commit()
    return playlist


@router.delete("/playlists/{playlist_id}")
def delete_playlist(playlist_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_playlist(conn, playlist_id):
        raise HTTPException(status_code=404, detail="Playlist not found")
    conn.commit()
    return {"message": "Playlist deleted"}


@router.get("/playlists/{playlist_id}/videos")
def playlist_videos(playlist_id: str, conn: sqlite3.Connection = Depends(get_db)):
    videos = db.playlist_videos(conn, playlist_id)
    if videos is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    theme_map = db.themes_for_videos(conn, [v["id"] for v in videos])
    for video in videos:
        video["themes"] = theme_map.get(video["id"], [])
    return {"playlist": db.get_playlist(conn, playlist_id), "videos": videos}


@router.post("/playlists/{playlist_id}/videos", status_code=201)
def add_playlist_video(
    playlist_id: str,
    body: PlaylistVideoRequest,
    conn: sqlite3.Connection = Depends(get_db),
):
    _editable_playlist(conn, playlist_id)
    if db.get_video(conn, body.video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")
    db.add_playlist_video(conn, playlist_id, body.video_id)
    conn.commit()
    return {"message": "Video added to playlist"}


@router.delete("/playlists/{playlist_id}/videos/{video_id}")
def remove_playlist_video(
    playlist_id: str, video_id: str, conn: sqlite3.Connection = Depends(get_db)
):
    _editable_playlist(conn, playlist_id)
    if not db.remove_playlist_video(conn, playlist_id, video_id):
        raise HTTPException(status_code=404, detail="Video not in playlist")
    conn.commit()
    return {"message": "Video removed from playlist"}


# --- theme rules -------------------------------------------------------------

@router.get("/rules")
def list_rules(conn: sqlite3.Connection = Depends(get_db)):
    return {"rules": db.list_theme_rules(conn)}


@router.post("/rules", status_code=201)
def create_rule(body: RuleCreateRequest, conn: sqlite3.Connection = Depends(get_db)):
    rule = db.add_theme_rule(conn, body.theme, body.pattern, body.exclusive)
    conn.commit()
    return rule


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, conn: sqlite3.Connection = Depends(get_db)):
    if not db.delete_theme_rule(conn, rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")
    conn.commit()
    return {"message": "Rule deleted"}


@router.get("/rules/suggestions")
def rule_suggestions(conn: sqlite3.Connection = Depends(get_db)):
    """Channel -> theme rules learned from videos you've already themed."""
    return {"suggestions": rules.suggest_rules(conn)}


@router.post("/rules/apply")
def apply_rules(conn: sqlite3.Connection = Depends(get_db)):
    """Re-run keyword + custom rules over every stored video."""
    return rules.reapply(conn)


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


# --- local downloads --------------------------------------------------------

@router.get("/downloads")
def list_downloads(
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Local copies, plus what this machine is actually capable of.

    The UI polls this while something is in flight, so live byte counts get
    merged in from memory here — they are never written to SQLite.
    """
    live = downloads.live_progress()
    items = db.list_downloads(conn)
    # Themes come along so the Offline tab can filter by category without a
    # second round trip per row.
    theme_map = db.themes_for_videos(conn, [i["video_id"] for i in items])
    for item in items:
        item["progress"] = live.get(item["video_id"])
        item["themes"] = theme_map.get(item["video_id"], [])
    return {
        "downloads": items,
        "usage": db.downloads_disk_usage(conn),
        "ffmpeg": ytdl.ffmpeg_available(),
        "default_height": settings.download_max_height,
        "quality_choices": ytdl.QUALITY_CHOICES,
        # Without ffmpeg every choice collapses to 360p. The UI shows this so
        # you don't pick 4K and quietly receive something far smaller.
        "max_height": ytdl.effective_height(
            max(ytdl.QUALITY_CHOICES), settings.ytdlp_player_client
        ),
    }


@router.post("/videos/{video_id}/download", status_code=202)
def start_download(
    video_id: str,
    body: Optional[DownloadRequest] = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Archive one video for offline watching; returns as soon as the job
    starts, since a 4K pull runs for minutes. Poll /downloads for progress."""
    body = body or DownloadRequest()
    if db.get_video(conn, video_id) is None:
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        return downloads.start(
            conn,
            video_id,
            settings.media_dir(),
            # None = no ceiling, i.e. whatever this video actually has.
            max_height=None if body.best
            else (body.max_height or settings.download_max_height),
            audio_only=body.audio_only,
            cookies_browser=settings.ytdlp_cookies_browser,
            player_client=settings.ytdlp_player_client,
        )
    except downloads.DownloadBusy as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/downloads/reveal")
def reveal_download(
    body: Optional[RevealRequest] = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Open the media folder in the OS file manager, selecting one video's
    file when asked. Local-only convenience — the app already runs on your
    desktop, so 'where is this file' should not need a manual dig."""
    body = body or RevealRequest()
    filename = None
    if body.video_id:
        row = db.get_download(conn, body.video_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No local copy of that video")
        filename = row["filename"]
    try:
        target = downloads.reveal(settings.media_dir(), filename)
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Could not open the folder: {e}")
    return {"opened": str(target)}


@router.post("/videos/{video_id}/play")
def record_play(video_id: str, conn: sqlite3.Connection = Depends(get_db)):
    """Count a play. Called when the player opens, so rewatches accumulate —
    this is what makes a favourite visible as a number."""
    count = db.record_play(conn, video_id)
    if count is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"video_id": video_id, "play_count": count}


@router.delete("/videos/{video_id}/download")
def delete_download(
    video_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Delete the local file and free the disk space. The video stays in the
    library — this only undoes the download."""
    try:
        removed = downloads.remove(conn, video_id, settings.media_dir())
    except downloads.DownloadBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="No local copy of that video")
    return {"message": "Local copy deleted"}
