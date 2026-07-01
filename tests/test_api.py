import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.ingest import sync as sync_module
from app.main import create_app

FAKE_VIDEOS = {
    "guitar000ok": {
        "id": "guitar000ok",
        "title": "Fingerstyle guitar lesson",
        "description": "Learn acoustic guitar",
        "channel_title": "GuitarChannel",
        "duration_sec": 900,
        "published_at": "2025-06-01T00:00:00Z",
        "tags": ["guitar", "acoustic"],
        "source": "api",
    },
    "aivideo00ok": {
        "id": "aivideo00ok",
        "title": "Machine learning explained",
        "description": None,
        "channel_title": "AIChannel",
        "duration_sec": 1200,
        "published_at": "2025-06-02T00:00:00Z",
        "tags": [],
        "source": "api",
    },
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    get_settings.cache_clear()

    def fake_fetch_metadata(video_ids, api_key):
        return [FAKE_VIDEOS[vid] for vid in video_ids if vid in FAKE_VIDEOS]

    monkeypatch.setattr(sync_module, "_fetch_metadata", fake_fetch_metadata)
    # playlist listing without an API key goes through yt-dlp; fake it
    monkeypatch.setattr(
        sync_module.ytdlp,
        "list_playlist",
        lambda url, cookies=None: {
            "title": "My Playlist",
            "video_ids": list(FAKE_VIDEOS.keys()),
        },
    )

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_add_video_applies_rule_themes(client):
    response = client.post(
        "/videos", json={"url": "https://youtu.be/guitar000ok"}
    )
    assert response.status_code == 201
    video = response.json()
    assert video["id"] == "guitar000ok"
    assert "Guitar" in video["themes"]
    assert video["watch_status"] == "unwatched"


def test_add_video_bad_url(client):
    response = client.post("/videos", json={"url": "https://example.com/x"})
    assert response.status_code == 400


def test_sync_playlist_idempotent(client):
    url = "https://www.youtube.com/playlist?list=PLtest123"
    first = client.post("/sync", json={"url": url}).json()
    assert first["added"] == 2
    assert first["total_in_source"] == 2
    assert first["title"] == "My Playlist"

    second = client.post("/sync", json={"url": url}).json()
    assert second["added"] == 0
    assert second["already_known"] == 2


def test_themes_and_listing(client):
    client.post("/videos", json={"url": "guitar000ok"})
    themes = client.get("/themes").json()["themes"]
    names = {t["name"] for t in themes}
    assert "Guitar" in names

    listing = client.get("/themes/Guitar/videos").json()
    assert [v["id"] for v in listing["videos"]] == ["guitar000ok"]

    assert client.get("/themes/Nonexistent/videos").status_code == 404


def test_watch_state_and_filtering(client):
    client.post("/videos", json={"url": "guitar000ok"})
    response = client.patch(
        "/videos/guitar000ok/watch-state", json={"status": "watched", "rating": 5}
    )
    assert response.status_code == 200
    assert response.json()["watch_status"] == "watched"
    assert response.json()["rating"] == 5

    unwatched = client.get("/themes/Guitar/videos?watched=false").json()["videos"]
    assert unwatched == []
    watched = client.get("/themes/Guitar/videos?watched=true").json()["videos"]
    assert len(watched) == 1

    assert client.patch("/videos/missing00id/watch-state", json={"status": "watched"}).status_code == 404
    assert client.patch("/videos/guitar000ok/watch-state", json={}).status_code == 400


def test_get_and_delete_video(client):
    client.post("/videos", json={"url": "aivideo00ok"})
    assert client.get("/videos/aivideo00ok").status_code == 200
    assert client.delete("/videos/aivideo00ok").status_code == 200
    assert client.get("/videos/aivideo00ok").status_code == 404
    assert client.delete("/videos/aivideo00ok").status_code == 404
