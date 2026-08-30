import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import db
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
    # env vars beat .env in pydantic-settings: set (not delete) these so a
    # developer's real .env can never leak keys/cookies into the tests
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("YTDLP_COOKIES_BROWSER", "")
    # create_app() creates the media folder on startup — keep that in tmp_path
    # instead of dropping one into the repo on every test run
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path / "media"))
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


def test_sync_counts_unavailable_videos(client, monkeypatch):
    # a listed id whose metadata can't be fetched (private/deleted) is
    # skipped and counted, not fatal
    monkeypatch.setattr(
        sync_module.ytdlp,
        "list_playlist",
        lambda url, cookies=None: {
            "title": "Mixed",
            "video_ids": list(FAKE_VIDEOS.keys()) + ["private000k"],
        },
    )
    result = client.post(
        "/sync", json={"url": "https://www.youtube.com/playlist?list=PLmixed"}
    ).json()
    assert result["added"] == 2
    assert result["unavailable"] == 1
    assert result["total_in_source"] == 3


def test_sync_stream_reports_progress(client):
    url = "https://www.youtube.com/playlist?list=PLtest123"
    response = client.post("/sync/stream", json={"url": url})
    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]

    stages = [l["progress"]["stage"] for l in lines if "progress" in l]
    assert stages[0] == "listing"
    assert "plan" in stages and "fetching" in stages and "storing" in stages
    plan = next(l["progress"] for l in lines if l.get("progress", {}).get("stage") == "plan")
    assert plan["total_in_source"] == 2 and plan["new"] == 2

    result = lines[-1]["result"]
    assert result["added"] == 2
    # the videos really landed
    assert client.get("/videos/guitar000ok").status_code == 200


def test_bulk_add_stream(client):
    text = (
        "https://youtu.be/guitar000ok?si=abc\n"
        "[https://youtu.be/aivideo00ok](https://youtu.be/aivideo00ok)\n"
        "guitar000ok, https://example.com/junk"
    )
    response = client.post("/videos/bulk/stream", json={"text": text})
    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]

    stages = [l["progress"]["stage"] for l in lines if "progress" in l]
    assert "plan" in stages and "fetching" in stages and "storing" in stages

    result = lines[-1]["result"]
    assert result["requested"] == 2  # duplicates collapse to one id each
    assert result["added"] == 2
    assert result["invalid"] == ["https://example.com/junk"]
    assert client.get("/videos/guitar000ok").status_code == 200
    assert client.get("/videos/aivideo00ok").status_code == 200

    # re-running the same list is idempotent
    again = client.post("/videos/bulk/stream", json={"text": text})
    result = json.loads(again.text.strip().splitlines()[-1])["result"]
    assert result["added"] == 0 and result["already_known"] == 2


def test_bulk_add_stream_no_valid_urls(client):
    response = client.post("/videos/bulk/stream", json={"text": "hello world"})
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert "error" in lines[-1]


def test_sync_stream_reports_errors(client):
    response = client.post("/sync/stream", json={"url": "https://example.com/x"})
    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines()]
    assert "error" in lines[-1]
    assert "recognize" in lines[-1]["error"].lower()


def test_themes_and_listing(client):
    client.post("/videos", json={"url": "guitar000ok"})
    themes = client.get("/themes").json()["themes"]
    names = {t["name"] for t in themes}
    assert "Guitar" in names

    listing = client.get("/themes/Guitar/videos").json()
    assert [v["id"] for v in listing["videos"]] == ["guitar000ok"]

    assert client.get("/themes/Nonexistent/videos").status_code == 404


def test_theme_assign_strips_whitespace(client):
    client.post("/videos", json={"url": "guitar000ok"})
    client.post("/videos/guitar000ok/themes", json={"name": "  Mental Health "})
    assert "Mental Health" in client.get("/videos/guitar000ok").json()["themes"]


def test_theme_rename_merge_delete(client):
    client.post("/videos", json={"url": "guitar000ok"})   # gets Guitar at ingest
    client.post("/videos", json={"url": "aivideo00ok"})   # gets AI at ingest
    client.post("/videos/guitar000ok/themes", json={"name": "Typo"})

    renamed = client.patch("/themes/Typo", json={"name": "Fixed"})
    assert renamed.status_code == 200
    assert renamed.json()["result"] == "renamed"
    assert "Fixed" in client.get("/videos/guitar000ok").json()["themes"]

    # renaming onto an existing theme merges the assignments
    merged = client.patch("/themes/Fixed", json={"name": "Guitar"})
    assert merged.json()["result"] == "merged"
    themes = client.get("/videos/guitar000ok").json()["themes"]
    assert themes.count("Guitar") == 1 and "Fixed" not in themes
    assert all(t["name"] != "Fixed" for t in client.get("/themes").json()["themes"])

    # deleting a theme removes assignments but keeps the videos
    assert client.delete("/themes/AI").status_code == 200
    assert client.get("/videos/aivideo00ok").status_code == 200
    assert client.get("/videos/aivideo00ok").json()["themes"] == []

    assert client.delete("/themes/AI").status_code == 404
    assert client.patch("/themes/Nope", json={"name": "X"}).status_code == 404


def test_watch_state_and_filtering(client):
    client.post("/videos", json={"url": "guitar000ok"})
    response = client.patch(
        "/videos/guitar000ok/watch-state", json={"status": "watched", "rating": 1}
    )
    assert response.status_code == 200
    assert response.json()["watch_status"] == "watched"
    assert response.json()["rating"] == 1

    unwatched = client.get("/themes/Guitar/videos?watched=false").json()["videos"]
    assert unwatched == []
    watched = client.get("/themes/Guitar/videos?watched=true").json()["videos"]
    assert len(watched) == 1

    assert client.patch("/videos/missing00id/watch-state", json={"status": "watched"}).status_code == 404
    assert client.patch("/videos/guitar000ok/watch-state", json={}).status_code == 400


def test_a_watched_write_survives_a_simultaneous_play_counter(client):
    """The end of a video sends two writes at once, and they used to collide.

    Both endpoints ran off one shared sqlite connection out of FastAPI's
    threadpool, so a pair landing in the same instant raced and sqlite3 raised
    "bad parameter or other API misuse" from whichever lost. That 500 dropped
    the mark-watched write at exactly the moment it mattered, leaving a video
    you had just finished sitting in the unwatched list.
    """
    client.post("/videos", json={"url": "guitar000ok"})
    client.post("/videos", json={"url": "aivideo00ok"})
    failures = []

    def call(send):
        try:
            response = send()
            if response.status_code != 200:
                failures.append((response.status_code, response.text[:200]))
        except Exception as e:                      # a raised InterfaceError
            failures.append(repr(e))

    threads = []
    for _ in range(10):
        threads.append(threading.Thread(target=call, args=(
            lambda: client.patch("/videos/guitar000ok/watch-state",
                                 json={"status": "watched"}),)))
        threads.append(threading.Thread(target=call, args=(
            lambda: client.post("/videos/aivideo00ok/play"),)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert failures == []
    assert client.get("/videos/guitar000ok").json()["watch_status"] == "watched"


def test_resume_position_survives_the_player(client):
    """Where a video stopped, kept so a dead embed costs seconds, not the video.

    The player used to leave nothing behind: an iframe that froze meant
    closing it, finding the card again and dragging the scrubber back to
    roughly the right place.
    """
    client.post("/videos", json={"url": "guitar000ok"})  # 900 seconds long
    stored = client.post("/videos/guitar000ok/position", json={"seconds": 312.4})
    assert stored.status_code == 200
    assert stored.json()["resume_seconds"] == 312.4
    assert client.get("/videos/guitar000ok").json()["resume_seconds"] == 312.4
    # and it travels with the listings the cards are built from
    listed = client.get("/videos").json()["videos"]
    assert next(v for v in listed if v["id"] == "guitar000ok")["resume_seconds"] == 312.4

    assert client.post("/videos/guitar000ok/position", json={"seconds": None}).json()[
        "resume_seconds"
    ] is None
    assert client.post("/videos/missing00id/position", json={"seconds": 10}).status_code == 404


def test_a_position_at_either_end_is_not_a_resume_point(client):
    """The start of a video and the end of one are both "nothing to resume".

    Dropping someone back at 0:08 reads as the app having lost the place
    rather than kept it, and a point in the last half-minute belongs to a
    video that is finished.
    """
    client.post("/videos", json={"url": "guitar000ok"})  # 900 seconds long
    url = "/videos/guitar000ok/position"
    assert client.post(url, json={"seconds": 8}).json()["resume_seconds"] is None
    assert client.post(url, json={"seconds": 880}).json()["resume_seconds"] is None
    assert client.post(url, json={"seconds": 400}).json()["resume_seconds"] == 400


def test_watching_a_video_retires_its_resume_point(client):
    """Otherwise a rewatch starts wherever you gave up the first time."""
    client.post("/videos", json={"url": "guitar000ok"})
    client.post("/videos/guitar000ok/position", json={"seconds": 400})
    watched = client.patch("/videos/guitar000ok/watch-state", json={"status": "watched"})
    assert watched.json()["resume_seconds"] is None
    # ...and unwatching it does not conjure the old position back
    again = client.patch("/videos/guitar000ok/watch-state", json={"status": "unwatched"})
    assert again.json()["resume_seconds"] is None


def test_player_events_reach_the_log(client, caplog):
    """The embed is cross-origin, so a freeze leaves no trace this side at all
    unless the page says so."""
    response = client.post(
        "/diagnostics/player-event",
        json={"event": "stalled", "video_id": "guitar000ok", "detail": "state=1 at=73"},
    )
    assert response.status_code == 204
    assert "player stalled: video=guitar000ok state=1 at=73" in caplog.text


def test_vote_is_thumbs_and_clearable(client):
    client.post("/videos", json={"url": "guitar000ok"})
    url = "/videos/guitar000ok/watch-state"

    assert client.patch(url, json={"rating": -1}).json()["rating"] == -1
    # 0 takes the thumb back without un-watching it: that's "it was okay"
    cleared = client.patch(url, json={"rating": 0, "status": "watched"}).json()
    assert cleared["rating"] is None
    assert cleared["watch_status"] == "watched"
    # a status-only update leaves the vote alone
    assert client.patch(url, json={"rating": 1}).json()["rating"] == 1
    assert client.patch(url, json={"status": "skipped"}).json()["rating"] == 1

    # stars are gone — the scale is only -1..1 now
    assert client.patch(url, json={"rating": 5}).status_code == 422
    assert client.patch(url, json={"rating": -2}).status_code == 422


def test_get_and_delete_video(client):
    client.post("/videos", json={"url": "aivideo00ok"})
    assert client.get("/videos/aivideo00ok").status_code == 200
    assert client.delete("/videos/aivideo00ok").status_code == 200
    assert client.get("/videos/aivideo00ok").status_code == 404
    assert client.delete("/videos/aivideo00ok").status_code == 404


def test_assign_and_remove_video_theme(client):
    client.post("/videos", json={"url": "guitar000ok"})
    added = client.post("/videos/guitar000ok/themes", json={"name": "Favorites"})
    assert added.status_code == 200
    assert "Favorites" in added.json()["themes"]

    removed = client.delete("/videos/guitar000ok/themes/Favorites")
    assert removed.status_code == 200
    assert "Favorites" not in removed.json()["themes"]
    assert client.delete("/videos/guitar000ok/themes/Favorites").status_code == 404


def test_bulk_assign_theme(client):
    client.post("/videos", json={"url": "guitar000ok"})
    client.post("/videos", json={"url": "aivideo00ok"})

    response = client.post(
        "/videos/themes/bulk",
        json={"video_ids": ["guitar000ok", "aivideo00ok", "missing00id"], "name": "Batch"},
    )
    assert response.status_code == 200
    assert response.json()["assigned"] == 2  # the unknown id is skipped

    assert "Batch" in client.get("/videos/guitar000ok").json()["themes"]
    assert "Batch" in client.get("/videos/aivideo00ok").json()["themes"]
    # empty selection is rejected by validation
    assert client.post(
        "/videos/themes/bulk", json={"video_ids": [], "name": "Batch"}
    ).status_code == 422


def test_rule_suggestion_learns_and_applies_to_future_videos(client):
    conn = client.app.state.db
    # the user tags three videos from one channel with the same theme
    for vid in ["megatech001", "megatech002", "megatech003"]:
        db.upsert_video(conn, {"id": vid, "title": "clip", "channel_title": "MegaTech"})
        db.assign_theme(conn, vid, db.get_or_create_theme(conn, "Homelab"), 1.0, "manual")
    conn.commit()

    suggestions = client.get("/rules/suggestions").json()["suggestions"]
    learned = next(s for s in suggestions if s["channel"] == "MegaTech")
    assert learned["theme"] == "Homelab"

    # approving the suggestion is just creating the rule; it should then theme a
    # brand-new video from that channel with no matching keywords in its title
    client.post("/rules", json={"pattern": learned["pattern"], "theme": learned["theme"]})
    db.upsert_video(conn, {"id": "megatech004", "title": "unrelated title", "channel_title": "MegaTech"})
    conn.commit()
    client.post("/rules/apply")
    assert "Homelab" in client.get("/videos/megatech004").json()["themes"]


def test_bulk_delete_videos(client):
    client.post("/videos", json={"url": "guitar000ok"})
    client.post("/videos", json={"url": "aivideo00ok"})

    response = client.post(
        "/videos/bulk/delete",
        json={"video_ids": ["guitar000ok", "aivideo00ok", "missing00id"]},
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 2  # unknown id doesn't count

    assert client.get("/videos/guitar000ok").status_code == 404
    assert client.get("/videos/aivideo00ok").status_code == 404
    # empty selection is rejected by validation
    assert client.post("/videos/bulk/delete", json={"video_ids": []}).status_code == 422


def test_renamed_builtin_theme_survives_resync(client):
    # regression: renaming a built-in theme used to revert on the next sync,
    # because the rule engine recreated the original name
    client.post("/videos", json={"url": "aivideo00ok"})  # gets built-in "AI"
    assert client.patch(
        "/themes/AI", json={"name": "Artificial Intelligence"}
    ).status_code == 200

    # re-theming the whole library must not bring "AI" back
    client.post("/rules/apply")
    names = [t["name"] for t in client.get("/themes").json()["themes"]]
    assert "AI" not in names
    assert client.get("/videos/aivideo00ok").json()["themes"] == [
        "Artificial Intelligence"
    ]


def test_rules_crud_and_retroactive_apply(client):
    # "Machine learning explained" matches the AI keyword theme at ingest
    client.post("/videos", json={"url": "aivideo00ok"})
    assert "AI" in client.get("/videos/aivideo00ok").json()["themes"]

    created = client.post(
        "/rules",
        json={"pattern": "machine learning", "theme": "ML_Only", "exclusive": True},
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]
    rules = client.get("/rules").json()["rules"]
    assert rules[0]["pattern"] == "machine learning"
    assert rules[0]["exclusive"] is True

    # retroactive apply: exclusive rule replaces every other theme
    result = client.post("/rules/apply").json()
    assert result["videos_scanned"] == 1
    assert result["themes_removed"] >= 1
    assert client.get("/videos/aivideo00ok").json()["themes"] == ["ML_Only"]

    assert client.delete(f"/rules/{rule_id}").status_code == 200
    assert client.delete(f"/rules/{rule_id}").status_code == 404
    assert client.get("/rules").json()["rules"] == []


def test_exclusive_rule_applies_at_ingest(client):
    client.post(
        "/rules",
        json={"pattern": "fingerstyle", "theme": "Fingerstyle", "exclusive": True},
    )
    client.post("/videos", json={"url": "guitar000ok"})
    # keyword themes (Guitar) are suppressed by the exclusive match
    assert client.get("/videos/guitar000ok").json()["themes"] == ["Fingerstyle"]


def test_list_all_videos_search_sort_filter(client):
    client.post("/videos", json={"url": "guitar000ok"})
    client.post("/videos", json={"url": "aivideo00ok"})

    listing = client.get("/videos").json()["videos"]
    assert [v["id"] for v in listing] == ["aivideo00ok", "guitar000ok"]  # newest first
    assert "Guitar" in listing[1]["themes"]  # themes attached across the board

    hits = client.get("/videos?search=machine").json()["videos"]
    assert [v["id"] for v in hits] == ["aivideo00ok"]
    assert client.get("/videos?search=nomatchxyz").json()["videos"] == []

    longest = client.get("/videos?sort=longest").json()["videos"]
    assert longest[0]["duration_sec"] == 1200
    by_channel = client.get("/videos?sort=channel").json()["videos"]
    assert by_channel[0]["channel_title"] == "AIChannel"

    client.patch("/videos/guitar000ok/watch-state", json={"status": "watched"})
    unwatched = client.get("/videos?watched=false").json()["videos"]
    assert [v["id"] for v in unwatched] == ["aivideo00ok"]


def test_filter_unthemed_videos(client):
    client.post("/videos", json={"url": "guitar000ok"})   # themed Guitar at ingest
    client.post("/videos", json={"url": "aivideo00ok"})   # themed AI at ingest
    client.delete("/videos/aivideo00ok/themes/AI")

    unthemed = client.get("/videos?unthemed=true").json()["videos"]
    assert [v["id"] for v in unthemed] == ["aivideo00ok"]
    themed = client.get("/videos?unthemed=false").json()["videos"]
    assert [v["id"] for v in themed] == ["guitar000ok"]
    both = client.get("/videos").json()["videos"]
    assert len(both) == 2


def test_filter_by_channel(client):
    client.post("/videos", json={"url": "guitar000ok"})   # GuitarChannel
    client.post("/videos", json={"url": "aivideo00ok"})   # AIChannel

    only = client.get("/videos?channel=GuitarChannel").json()["videos"]
    assert [v["id"] for v in only] == ["guitar000ok"]
    # the name comes off a card, so it must not be case-sensitive
    assert client.get("/videos?channel=guitarchannel").json()["videos"] == only
    # and it stacks with the other browse filters instead of replacing them
    client.patch("/videos/guitar000ok/watch-state", json={"status": "watched"})
    assert client.get("/videos?channel=GuitarChannel&watched=false").json()["videos"] == []


def test_channel_filter_spans_renames_and_missing_ids(client):
    """A channel is matched by id OR name: rows predate a rename, and yt-dlp
    rows can carry the name with no id. Either test alone shows half of it."""
    conn = client.app.state.db
    db.upsert_video(conn, {"id": "renamed0001", "title": "old upload",
                           "channel_id": "UCchannel00", "channel_title": "Old Name"})
    db.upsert_video(conn, {"id": "current0002", "title": "new upload",
                           "channel_id": "UCchannel00", "channel_title": "New Name"})
    db.upsert_video(conn, {"id": "noidrow0003", "title": "listed by yt-dlp",
                           "channel_title": "New Name"})
    db.upsert_video(conn, {"id": "unrelated04", "title": "somebody else",
                           "channel_id": "UCother0000", "channel_title": "Other"})
    conn.commit()

    hits = client.get("/videos?channel=New+Name&channel_id=UCchannel00").json()["videos"]
    assert {v["id"] for v in hits} == {"renamed0001", "current0002", "noidrow0003"}


def test_themes_response_includes_total_videos(client):
    assert client.get("/themes").json()["total_videos"] == 0
    client.post("/videos", json={"url": "guitar000ok"})
    assert client.get("/themes").json()["total_videos"] == 1


def test_theme_counts_follow_watched_filter(client):
    client.post("/videos", json={"url": "guitar000ok"})  # auto-themed "Guitar"

    def guitar_count(query=""):
        themes = client.get(f"/themes{query}").json()["themes"]
        return next(t["video_count"] for t in themes if t["name"] == "Guitar")

    # unfiltered and unwatched-only agree while nothing is watched
    assert guitar_count() == 1
    assert guitar_count("?watched=false") == 1
    assert client.get("/themes?watched=false").json()["total_videos"] == 1

    client.patch("/videos/guitar000ok/watch-state", json={"status": "watched"})

    # total count is unchanged; unwatched-only count drops to 0
    assert guitar_count() == 1
    assert guitar_count("?watched=false") == 0
    assert guitar_count("?watched=true") == 1
    assert client.get("/themes?watched=false").json()["total_videos"] == 0
    assert client.get("/themes").json()["total_videos"] == 1


def test_local_playlists_crud(client):
    client.post("/videos", json={"url": "guitar000ok"})
    client.post("/videos", json={"url": "aivideo00ok"})

    created = client.post("/playlists", json={"title": "Watch next"})
    assert created.status_code == 201
    playlist = created.json()
    assert playlist["id"].startswith("local-")
    assert playlist["kind"] == "local"
    pid = playlist["id"]

    # add both, order preserved
    assert client.post(f"/playlists/{pid}/videos", json={"video_id": "guitar000ok"}).status_code == 201
    assert client.post(f"/playlists/{pid}/videos", json={"video_id": "aivideo00ok"}).status_code == 201
    listing = client.get(f"/playlists/{pid}/videos").json()
    assert [v["id"] for v in listing["videos"]] == ["guitar000ok", "aivideo00ok"]

    mine = next(p for p in client.get("/playlists").json()["playlists"] if p["id"] == pid)
    assert mine["video_count"] == 2

    # remove one video, then the playlist itself
    assert client.delete(f"/playlists/{pid}/videos/guitar000ok").status_code == 200
    remaining = client.get(f"/playlists/{pid}/videos").json()["videos"]
    assert [v["id"] for v in remaining] == ["aivideo00ok"]
    assert client.delete(f"/playlists/{pid}").status_code == 200
    assert client.get(f"/playlists/{pid}/videos").status_code == 404
    # deleting the playlist never deletes the videos
    assert client.get("/videos/aivideo00ok").status_code == 200


def test_playlist_edit_guards(client):
    client.post("/videos", json={"url": "guitar000ok"})
    # synced playlists are read-only (re-sync would overwrite local edits)
    client.post("/sync", json={"url": "https://www.youtube.com/playlist?list=PLtest123"})
    denied = client.post("/playlists/PLtest123/videos", json={"video_id": "guitar000ok"})
    assert denied.status_code == 400

    pid = client.post("/playlists", json={"title": "Q"}).json()["id"]
    assert client.post(f"/playlists/{pid}/videos", json={"video_id": "missing00id"}).status_code == 404
    assert client.post("/playlists/local-nope/videos", json={"video_id": "guitar000ok"}).status_code == 404
    assert client.delete(f"/playlists/{pid}/videos/guitar000ok").status_code == 404


def test_rule_validation(client):
    assert client.post("/rules", json={"pattern": "  ", "theme": "X"}).status_code == 422
    assert client.post("/rules", json={"pattern": "x", "theme": ""}).status_code == 422
