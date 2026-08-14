"""Download tests. Like the rest of the suite these never touch the network:
the yt-dlp call is faked at the `download_video` boundary, so what's covered
here is the format logic, the job bookkeeping, and the file lifecycle.
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db, downloads
from app.config import get_settings
from app.ingest import download as ytdl
from app.main import create_app

VIDEO = {
    "id": "vid00000001",
    "title": "A test video",
    "channel_title": "TestChannel",
    "duration_sec": 120,
    "source": "api",
}


# --- format selection -------------------------------------------------------

def test_without_ffmpeg_everything_collapses_to_progressive(monkeypatch):
    monkeypatch.setattr(ytdl, "ffmpeg_available", lambda: False)
    assert ytdl.effective_height(2160) == ytdl.NO_FFMPEG_MAX_HEIGHT
    selector = ytdl._format_selector(2160, audio_only=False)
    # No "+" means no merge step, which is the whole point of the fallback.
    assert "+" not in selector
    assert f"height<={ytdl.NO_FFMPEG_MAX_HEIGHT}" in selector


def test_with_ffmpeg_asks_for_separate_streams(monkeypatch):
    monkeypatch.setattr(ytdl, "ffmpeg_available", lambda: True)
    assert ytdl.effective_height(2160) == 2160
    selector = ytdl._format_selector(1440, audio_only=False)
    assert "bv*[height<=1440]" in selector


def test_codec_preference_never_constrains_the_selector(monkeypatch):
    """Asking for 4K must not come back 1080p.

    H.264 only exists up to 1080p on YouTube, so naming it in the selector
    made the 1080p branch match before any taller VP9/AV1 stream was
    considered. Codec preference belongs in format_sort, where it ranks
    alternatives at the same height instead of excluding taller ones.
    """
    monkeypatch.setattr(ytdl, "ffmpeg_available", lambda: True)
    selector = ytdl._format_selector(2160, audio_only=False)
    assert "avc1" not in selector
    assert "vcodec" not in selector
    # ...but the preference still exists, ranked below resolution
    assert ytdl.FORMAT_SORT[0] == "res"
    assert any("h264" in field for field in ytdl.FORMAT_SORT)


def test_best_puts_no_ceiling_on_height(monkeypatch):
    monkeypatch.setattr(ytdl, "ffmpeg_available", lambda: True)
    selector = ytdl._format_selector(None, audio_only=False)
    assert "height" not in selector
    assert selector.startswith("bv*+ba")
    assert ytdl.effective_height(None) is None


def test_best_still_degrades_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(ytdl, "ffmpeg_available", lambda: False)
    assert ytdl.effective_height(None) == ytdl.NO_FFMPEG_MAX_HEIGHT
    selector = ytdl._format_selector(None, audio_only=False)
    assert f"height<={ytdl.NO_FFMPEG_MAX_HEIGHT}" in selector
    assert "+" not in selector


def test_audio_only_never_needs_ffmpeg(monkeypatch):
    monkeypatch.setattr(ytdl, "ffmpeg_available", lambda: False)
    selector = ytdl._format_selector(2160, audio_only=True)
    assert selector.startswith("bestaudio")
    assert "+" not in selector


def test_progress_bridge_never_goes_backwards():
    seen = []
    bridge = ytdl._ProgressBridge(seen.append)
    # video stream
    bridge({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
    bridge({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
    bridge({"status": "finished", "total_bytes": 100})
    # audio stream starts over at 0 in yt-dlp's own accounting
    bridge({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 20})
    counts = [e["done"] for e in seen]
    assert counts == sorted(counts), counts
    assert counts[-1] == 105


# --- the media folder boundary ---------------------------------------------

def test_media_file_refuses_to_escape_the_folder(tmp_path):
    assert downloads.media_file(tmp_path, "abc.mp4") == (tmp_path / "abc.mp4").resolve()
    # A row holding a traversal must not let a delete reach outside.
    assert downloads.media_file(tmp_path, "../abc.mp4").parent == tmp_path.resolve()


# --- API --------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("YTDLP_COOKIES_BROWSER", "")
    monkeypatch.setenv("DOWNLOAD_MAX_HEIGHT", "1440")
    get_settings.cache_clear()

    def fake_download(video_id, media_dir, max_height=1440, audio_only=False,
                      cookies_browser=None, progress=None):
        media_dir.mkdir(parents=True, exist_ok=True)
        path = media_dir / f"{video_id}.{'m4a' if audio_only else 'mp4'}"
        path.write_bytes(b"\0" * 4096)
        if progress:
            progress({"stage": "downloading", "done": 2048, "total": 4096})
        if audio_only:
            height = None
        elif max_height is None:
            height = 2160          # "best" — stand-in for a 4K source
        else:
            height = min(max_height, 1080)   # this fake video tops out at 1080p
        return {"filename": path.name, "size_bytes": 4096, "height": height}

    monkeypatch.setattr(ytdl, "download_video", fake_download)

    app = create_app()
    with TestClient(app) as c:
        db.upsert_video(app.state.db, VIDEO)
        app.state.db.commit()
        c.media = tmp_path / "media"
        yield c
    get_settings.cache_clear()


def wait_for(client, status, timeout=5.0):
    """Downloads run on a background thread, so poll the way the UI does."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = client.get("/downloads").json()["downloads"]
        if rows and rows[0]["status"] == status:
            return rows[0]
        time.sleep(0.02)
    raise AssertionError(f"never reached {status}: {client.get('/downloads').json()}")


def test_download_lands_on_disk_and_in_the_row(client):
    assert client.post("/videos/vid00000001/download", json={}).status_code == 202
    row = wait_for(client, "done")
    assert row["filename"] == "vid00000001.mp4"
    assert row["size_bytes"] == 4096
    assert (client.media / "vid00000001.mp4").is_file()


def test_requested_height_reaches_yt_dlp(client):
    client.post("/videos/vid00000001/download", json={"max_height": 720})
    assert wait_for(client, "done")["height"] == 720


def test_default_height_comes_from_settings(client):
    body = client.post("/videos/vid00000001/download", json={}).json()
    assert body["requested_height"] == 1440
    wait_for(client, "done")


def test_best_asks_for_no_ceiling_at_all(client):
    """`best` has to reach yt-dlp as None, not as the configured default —
    otherwise a 4K video would come back capped at 2K."""
    seen = {}

    def capture(video_id, media_dir, max_height=1440, audio_only=False, **kwargs):
        seen["max_height"] = max_height
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / f"{video_id}.mp4").write_bytes(b"\0" * 8)
        return {"filename": f"{video_id}.mp4", "size_bytes": 8, "height": 2160}

    ytdl_download = ytdl.download_video
    try:
        ytdl.download_video = capture
        body = client.post("/videos/vid00000001/download", json={"best": True}).json()
        assert body["requested_height"] is None
        assert wait_for(client, "done")["height"] == 2160
    finally:
        ytdl.download_video = ytdl_download
    assert seen["max_height"] is None


def test_best_overrides_an_explicit_height(client):
    body = client.post(
        "/videos/vid00000001/download", json={"best": True, "max_height": 480}
    ).json()
    assert body["requested_height"] is None
    wait_for(client, "done")


def test_audio_only_is_recorded_and_named_m4a(client):
    client.post("/videos/vid00000001/download", json={"audio_only": True})
    row = wait_for(client, "done")
    assert row["audio_only"] == 1
    assert row["filename"].endswith(".m4a")
    assert row["height"] is None


def test_video_endpoints_expose_the_local_copy(client):
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    single = client.get("/videos/vid00000001").json()
    assert single["download_status"] == "done"
    assert single["download_file"] == "vid00000001.mp4"
    # the browse grid needs it too, or cards can't show the badge
    listed = client.get("/videos").json()["videos"][0]
    assert listed["download_status"] == "done"


def test_media_is_served_with_range_support(client):
    """Seeking a local file depends on 206s — a 200-only route would give you
    a video you can only play start to finish."""
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    whole = client.get("/media/vid00000001.mp4")
    assert whole.status_code == 200
    assert len(whole.content) == 4096
    part = client.get("/media/vid00000001.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100


def test_deleting_the_copy_frees_the_file_but_keeps_the_video(client):
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    assert client.delete("/videos/vid00000001/download").status_code == 200
    assert not (client.media / "vid00000001.mp4").exists()
    assert client.get("/videos/vid00000001").json()["download_status"] is None
    assert client.get("/downloads").json()["downloads"] == []


def test_deleting_the_video_also_removes_the_media(client):
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    client.delete("/videos/vid00000001")
    assert not (client.media / "vid00000001.mp4").exists()


def test_bulk_delete_removes_media_too(client):
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    r = client.post("/videos/bulk/delete", json={"video_ids": ["vid00000001"]})
    assert r.json()["media_removed"] == 1
    assert not (client.media / "vid00000001.mp4").exists()


def test_unknown_video_is_404(client):
    assert client.post("/videos/nosuchvideo/download", json={}).status_code == 404


def test_deleting_a_copy_that_is_not_there_is_404(client):
    assert client.delete("/videos/vid00000001/download").status_code == 404


def test_a_second_request_for_the_same_video_is_rejected(client, monkeypatch):
    """One download per video: a concurrent second run would have both threads
    writing the same path."""
    release = threading.Event()

    def blocking(video_id, media_dir, **kwargs):
        release.wait(timeout=5)
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / f"{video_id}.mp4").write_bytes(b"\0")
        return {"filename": f"{video_id}.mp4", "size_bytes": 1, "height": 360}

    monkeypatch.setattr(ytdl, "download_video", blocking)
    assert client.post("/videos/vid00000001/download", json={}).status_code == 202
    assert client.post("/videos/vid00000001/download", json={}).status_code == 409
    # a delete can't race the writer either
    assert client.delete("/videos/vid00000001/download").status_code == 409
    release.set()
    wait_for(client, "done")


def test_failure_is_recorded_rather_than_lost(client, monkeypatch):
    def boom(*args, **kwargs):
        raise ytdl.DownloadError("Video unavailable")

    monkeypatch.setattr(ytdl, "download_video", boom)
    client.post("/videos/vid00000001/download", json={})
    row = wait_for(client, "error")
    assert "Video unavailable" in row["error"]
    assert row["filename"] is None


# --- offline as a favourites shelf -----------------------------------------

def test_offline_filter_narrows_browse_and_themes(client):
    conn = client.app.state.db
    db.upsert_video(conn, {**VIDEO, "id": "vid00000002", "title": "Not saved"})
    theme_id = db.get_or_create_theme(conn, "Music")
    db.assign_theme(conn, "vid00000001", theme_id, 1.0, "manual")
    db.assign_theme(conn, "vid00000002", theme_id, 1.0, "manual")
    conn.commit()
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")

    everything = client.get("/videos").json()["videos"]
    assert len(everything) == 2
    offline = client.get("/videos?downloaded=true").json()["videos"]
    assert [v["id"] for v in offline] == ["vid00000001"]
    # the same filter has to work inside a category, or "offline" and
    # "category" can't be combined
    in_theme = client.get("/themes/Music/videos?downloaded=true").json()["videos"]
    assert [v["id"] for v in in_theme] == ["vid00000001"]
    assert len(client.get("/themes/Music/videos").json()["videos"]) == 2


def test_a_queued_download_is_not_offline_yet(client, monkeypatch):
    """Only a finished copy counts: a queued or failed one has no file, so it
    would be a dead entry in an offline view."""
    def forbidden(*args, **kwargs):
        raise ytdl.DownloadError("nope")

    monkeypatch.setattr(ytdl, "download_video", forbidden)
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "error")
    assert client.get("/videos?downloaded=true").json()["videos"] == []


def test_plays_accumulate_and_are_exposed(client):
    assert client.post("/videos/vid00000001/play").json()["play_count"] == 1
    assert client.post("/videos/vid00000001/play").json()["play_count"] == 2
    video = client.get("/videos/vid00000001").json()
    assert video["play_count"] == 2
    assert video["last_played_at"]
    assert client.get("/videos").json()["videos"][0]["play_count"] == 2


def test_a_play_does_not_touch_watch_state(client):
    """Reaching for something again says nothing about whether you consider it
    watched, and must never clear a thumb you already gave."""
    client.patch("/videos/vid00000001/watch-state", json={"status": "watched", "rating": 1})
    client.post("/videos/vid00000001/play")
    video = client.get("/videos/vid00000001").json()
    assert video["watch_status"] == "watched"
    assert video["rating"] == 1
    assert video["play_count"] == 1


def test_never_played_videos_report_zero_not_null(client):
    assert client.get("/videos/vid00000001").json()["play_count"] == 0


def test_play_on_unknown_video_is_404(client):
    assert client.post("/videos/nosuchvideo/play").status_code == 404


def test_most_watched_sort_ranks_by_play_count(client):
    conn = client.app.state.db
    db.upsert_video(conn, {**VIDEO, "id": "vid00000002", "title": "Played more"})
    conn.commit()
    for _ in range(3):
        client.post("/videos/vid00000002/play")
    client.post("/videos/vid00000001/play")
    ids = [v["id"] for v in client.get("/videos?sort=played").json()["videos"]]
    assert ids[:2] == ["vid00000002", "vid00000001"]


def test_downloads_list_carries_themes_and_plays(client):
    conn = client.app.state.db
    theme_id = db.get_or_create_theme(conn, "Music")
    db.assign_theme(conn, "vid00000001", theme_id, 1.0, "manual")
    conn.commit()
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    client.post("/videos/vid00000001/play")
    row = client.get("/downloads").json()["downloads"][0]
    assert row["themes"] == ["Music"]     # category filter in the Offline tab
    assert row["play_count"] == 1


def test_reveal_opens_the_media_folder(client, monkeypatch):
    opened = []
    monkeypatch.setattr(downloads.subprocess, "Popen", lambda args, **kw: opened.append(args))
    assert client.post("/downloads/reveal", json={}).status_code == 200
    assert str(client.media) in " ".join(opened[0])


def test_reveal_selects_a_specific_file(client, monkeypatch):
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    opened = []
    monkeypatch.setattr(downloads.subprocess, "Popen", lambda args, **kw: opened.append(args))
    client.post("/downloads/reveal", json={"video_id": "vid00000001"})
    assert "vid00000001.mp4" in " ".join(opened[0])


def test_reveal_without_a_copy_is_404(client):
    assert client.post("/downloads/reveal", json={"video_id": "vid00000001"}).status_code == 404


def test_capabilities_are_reported(client):
    data = client.get("/downloads").json()
    assert data["default_height"] == 1440
    assert 2160 in data["quality_choices"]
    assert isinstance(data["ffmpeg"], bool)
    # what the machine can really do, so the UI can label the rest
    assert data["max_height"] == (2160 if data["ffmpeg"] else 360)


def test_disk_usage_counts_finished_copies_only(client, monkeypatch):
    client.post("/videos/vid00000001/download", json={})
    wait_for(client, "done")
    assert client.get("/downloads").json()["usage"] == {"files": 1, "bytes": 4096}


def test_restart_clears_downloads_left_in_flight(client):
    """Nothing is resumable across a restart, so a row still claiming
    'downloading' would spin in the UI forever."""
    conn = client.app.state.db
    db.mark_download_queued(conn, "vid00000001")
    db.mark_download_running(conn, "vid00000001")
    assert downloads.reset_stale(conn) == 1
    row = db.get_download(conn, "vid00000001")
    assert row["status"] == "error"
    assert "restart" in row["error"]


def test_redownloading_replaces_the_previous_row(client):
    client.post("/videos/vid00000001/download", json={"audio_only": True})
    wait_for(client, "done")
    client.post("/videos/vid00000001/download", json={"max_height": 720})
    wait_for(client, "done")
    rows = client.get("/downloads").json()["downloads"]
    assert len(rows) == 1  # one video, one local copy
    assert rows[0]["audio_only"] == 0
    assert rows[0]["height"] == 720


def test_a_failed_redownload_keeps_the_copy_you_had(client, monkeypatch):
    """The whole point of re-downloading is to get something better. Failing
    must not leave you with less than you started with — YouTube 403s on the
    adaptive streams often enough that this is a routine path, not an edge."""
    client.post("/videos/vid00000001/download", json={"max_height": 360})
    wait_for(client, "done")

    def forbidden(*args, **kwargs):
        raise ytdl.DownloadError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(ytdl, "download_video", forbidden)
    client.post("/videos/vid00000001/download", json={"max_height": 1080})
    row = wait_for(client, "done")           # still 'done', not 'error'
    assert row["height"] == 360              # the copy that survived
    assert row["filename"] == "vid00000001.mp4"
    assert (client.media / "vid00000001.mp4").is_file()
    assert "403" in row["error"]             # but the failure is still reported
    # and it is still playable
    assert client.get("/media/vid00000001.mp4").status_code == 200


def test_a_first_download_that_fails_has_nothing_to_keep(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise ytdl.DownloadError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(ytdl, "download_video", forbidden)
    client.post("/videos/vid00000001/download", json={})
    row = wait_for(client, "error")
    assert row["filename"] is None


def test_partial_files_are_cleaned_up_after_a_failure(client, monkeypatch):
    """yt-dlp leaves `{id}.f137.mp4.part` behind when a stream dies midway;
    nothing in the downloads table points at those, so they would just pile
    up in the media folder."""
    def dies_midway(video_id, media_dir, **kwargs):
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / f"{video_id}.f137.mp4.part").write_bytes(b"\0" * 999)
        (media_dir / f"{video_id}.f137.mp4.ytdl").write_bytes(b"{}")
        raise ytdl.DownloadError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(ytdl, "download_video", dies_midway)
    client.post("/videos/vid00000001/download", json={"max_height": 1080})
    wait_for(client, "error")
    assert list(client.media.iterdir()) == []


def test_redownloading_does_not_strand_the_old_file(client):
    """Switching between audio and video changes the extension, so the old
    file is not the one yt-dlp overwrites — it has to be removed explicitly."""
    client.post("/videos/vid00000001/download", json={"audio_only": True})
    wait_for(client, "done")
    assert (client.media / "vid00000001.m4a").is_file()
    client.post("/videos/vid00000001/download", json={"max_height": 1080})
    wait_for(client, "done")
    assert (client.media / "vid00000001.mp4").is_file()
    assert not (client.media / "vid00000001.m4a").exists()
    assert [p.name for p in client.media.iterdir()] == ["vid00000001.mp4"]
