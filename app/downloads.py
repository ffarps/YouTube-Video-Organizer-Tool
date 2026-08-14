"""Background download jobs: start one, poll it, delete the file after.

A 4K download runs for minutes, so the request that starts one returns
immediately and the UI polls `/downloads` instead of holding a connection open
the way `/sync/stream` does. Durable state (queued / downloading / done /
error) lives in the `downloads` table; only the live byte counter is kept in
memory, because writing progress to SQLite several times a second would be a
lot of churn for a number nobody needs once the file has landed.
"""
import logging
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

from app import db
from app.ingest import download as ytdl

log = logging.getLogger("watchlog.downloads")

# video_id -> the most recent progress event for downloads running right now.
_active: Dict[str, dict] = {}
_lock = threading.Lock()


class DownloadBusy(Exception):
    """This video is already being fetched."""


def is_active(video_id: str) -> bool:
    with _lock:
        return video_id in _active


def live_progress() -> Dict[str, dict]:
    """Snapshot of in-flight byte counts, keyed by video id."""
    with _lock:
        return dict(_active)


def reset_stale(conn: sqlite3.Connection) -> int:
    """Fail any download left mid-flight by a previous process.

    Nothing is resumable across a restart — the in-memory job is gone and the
    partial file was written with `continuedl: False` — so a row still
    claiming 'downloading' at startup is a lie that would otherwise show a
    stuck spinner forever.
    """
    cur = conn.execute(
        """
        UPDATE downloads
           SET status = 'error', error = 'Interrupted by app restart'
         WHERE status IN ('queued', 'downloading')
        """
    )
    conn.commit()
    return cur.rowcount


def media_file(media_dir: Path, filename: str) -> Path:
    """Resolve a stored filename inside the media directory.

    Filenames come from our own `{video_id}.{ext}` template, but this is the
    boundary between the database and the filesystem, so a row that somehow
    holds a path separator or `..` must not reach outside the media folder.
    """
    resolved = (media_dir / Path(filename).name).resolve()
    if resolved.parent != media_dir.resolve():
        raise ValueError(f"Refusing to touch {filename} outside the media folder")
    return resolved


def _unlink(media_dir: Path, filename: str) -> bool:
    try:
        media_file(media_dir, filename).unlink(missing_ok=True)
        return True
    except (OSError, ValueError):
        # A file we can't remove is a stray, never a reason to fail the run
        # that produced a perfectly good download.
        return False


def _clean_partials(media_dir: Path, video_id: str) -> int:
    """Remove yt-dlp's scratch files for a video.

    An interrupted run leaves `{id}.f137.mp4.part` and friends behind, which
    are invisible to the downloads table and would otherwise accumulate as
    dead weight in the media folder.
    """
    removed = 0
    for pattern in (f"{video_id}*.part", f"{video_id}*.ytdl"):
        for path in media_dir.glob(pattern):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
    return removed


def reveal(media_dir: Path, filename: Optional[str] = None) -> Path:
    """Open the media folder in the desktop file manager.

    With a filename, the file is selected rather than just its folder opened.
    Only ever points at MEDIA_PATH or something inside it — `media_file` is
    what keeps a database row from steering this at an arbitrary path.
    """
    media_dir.mkdir(parents=True, exist_ok=True)
    target = media_file(media_dir, filename) if filename else media_dir.resolve()
    if filename and not target.exists():
        target = media_dir.resolve()  # file is gone; the folder is still useful

    if sys.platform == "win32":
        if target.is_file():
            # explorer.exe exits 1 even when it works, so its status is not
            # worth checking — hence Popen rather than run(check=True).
            subprocess.Popen(["explorer", f"/select,{target}"])
        else:
            subprocess.Popen(["explorer", str(target)])
    elif sys.platform == "darwin":
        subprocess.Popen(
            ["open", "-R", str(target)] if target.is_file() else ["open", str(target)]
        )
    else:
        # Linux file managers have no portable "select this file" flag.
        parent = target.parent if target.is_file() else target
        subprocess.Popen(["xdg-open", str(parent)])
    return target


def start(
    conn: sqlite3.Connection,
    video_id: str,
    media_dir: Path,
    max_height: Optional[int],
    audio_only: bool = False,
    cookies_browser: Optional[str] = None,
) -> dict:
    """Queue a download and return once the worker thread is running.

    `max_height=None` means "the best this video offers". Raises DownloadBusy
    if this video is already in flight.
    """
    with _lock:
        if video_id in _active:
            raise DownloadBusy(f"{video_id} is already downloading")
        _active[video_id] = {"stage": "queued", "done": 0, "total": 0}

    # A copy worth keeping if this run fails. Re-downloading for a better
    # quality is the common case, and YouTube 403s on the adaptive streams
    # often enough that deleting first would regularly trade a working file
    # for nothing.
    previous = db.get_download(conn, video_id)
    if not (previous and previous["status"] == "done" and previous["filename"]):
        previous = None
    db.mark_download_queued(conn, video_id, audio_only)

    def on_progress(event: dict) -> None:
        with _lock:
            if video_id in _active:
                _active[video_id] = event

    def run() -> None:
        try:
            log.info(
                "download start %s (max_height=%s audio_only=%s)",
                video_id,
                max_height,
                audio_only,
            )
            db.mark_download_running(conn, video_id)
            result = ytdl.download_video(
                video_id,
                media_dir,
                max_height=max_height,
                audio_only=audio_only,
                cookies_browser=cookies_browser,
                progress=on_progress,
            )
            db.mark_download_done(
                conn,
                video_id,
                result["filename"],
                result["size_bytes"],
                result["height"],
            )
            # Only now is the old copy expendable. Same-name writes were
            # already replaced by yt-dlp; a name change (mp4 <-> m4a) leaves
            # the old file behind unless it is removed here.
            if previous and previous["filename"] != result["filename"]:
                _unlink(media_dir, previous["filename"])
            log.info(
                "download done %s -> %s (%d bytes)",
                video_id,
                result["filename"],
                result["size_bytes"],
            )
        except Exception as e:  # any failure must clear the spinner
            message = f"{type(e).__name__}: {e}"
            # The row keeps one line for the UI; the traceback only exists here.
            log.exception("download failed %s: %s", video_id, message)
            if previous:
                db.restore_download(conn, video_id, previous, message)
            else:
                db.mark_download_failed(conn, video_id, message)
        finally:
            _clean_partials(media_dir, video_id)
            with _lock:
                _active.pop(video_id, None)

    threading.Thread(target=run, daemon=True, name=f"download-{video_id}").start()
    return {
        "video_id": video_id,
        "status": "queued",
        "audio_only": audio_only,
        "requested_height": max_height,
        # What will actually land, which is lower when ffmpeg is missing.
        "effective_height": None if audio_only else ytdl.effective_height(max_height),
    }


def remove(conn: sqlite3.Connection, video_id: str, media_dir: Path) -> bool:
    """Delete the local copy and forget it. Returns False if there wasn't one.

    A download still running is left alone: killing the yt-dlp thread mid-write
    would leave a partial file with no row pointing at it.
    """
    if is_active(video_id):
        raise DownloadBusy(f"{video_id} is still downloading")
    filename = db.delete_download(conn, video_id)
    _clean_partials(media_dir, video_id)
    if filename is None:
        return False
    _unlink(media_dir, filename)
    return True


def remove_files_for(
    conn: sqlite3.Connection, video_ids, media_dir: Path
) -> int:
    """Unlink media for videos about to be deleted from the library.

    `ON DELETE CASCADE` takes the `downloads` row but knows nothing about the
    filesystem, so this has to run *before* the videos are deleted.
    """
    removed = 0
    for filename in db.downloaded_filenames(conn, video_ids):
        if _unlink(media_dir, filename):
            removed += 1
    for video_id in video_ids:
        _clean_partials(media_dir, video_id)
    return removed
