from app import db
from tests.conftest import make_video


def _events(conn, video_id):
    return [
        (r["event"], r["value"])
        for r in conn.execute(
            "SELECT event, value FROM history_events WHERE video_id = ? ORDER BY id",
            (video_id,),
        )
    ]


def test_history_logs_changes_not_repeats(conn):
    make_video(conn, "aaaaaaaaaaa", duration_sec=600)
    db.record_play(conn, "aaaaaaaaaaa")
    db.set_watch_state(conn, "aaaaaaaaaaa", status="watched")
    # a thumb re-sends status=watched: only the vote is new
    db.set_watch_state(conn, "aaaaaaaaaaa", status="watched", rating=1)
    db.set_watch_state(conn, "aaaaaaaaaaa", status="watched", rating=1)
    db.set_watch_state(conn, "aaaaaaaaaaa", rating=0)
    assert _events(conn, "aaaaaaaaaaa") == [
        ("play", None), ("watched", None), ("rated", 1), ("rated", 0),
    ]


def test_history_lists_only_videos_with_activity(conn):
    make_video(conn, "touched0000", title="touched")
    make_video(conn, "untouched00", title="untouched")
    db.record_play(conn, "touched0000")
    items = db.watch_history(conn)
    assert [i["video_id"] for i in items] == ["touched0000"]
    assert items[0]["play_count"] == 1
    assert items[0]["last_activity"]


def test_a_deleted_video_keeps_its_history(conn):
    make_video(conn, "gone0000000", title="Gone", channel_title="Chan", duration_sec=300)
    make_video(conn, "neverseen00", title="Never seen")
    db.record_play(conn, "gone0000000")
    db.record_play(conn, "gone0000000")
    db.set_watch_state(conn, "gone0000000", status="watched", rating=-1)
    db.delete_video(conn, "gone0000000")
    db.delete_videos(conn, ["neverseen00"])
    conn.commit()

    (row,) = db.watch_history(conn)
    assert row["video_id"] == "gone0000000"
    assert row["title"] == "Gone"
    assert row["channel_title"] == "Chan"
    assert (row["status"], row["rating"], row["play_count"]) == ("watched", -1, 2)
    assert row["deleted_at"]

    # the never-watched one is clean-up: only the deleted filter shows it
    deleted = {i["video_id"] for i in db.watch_history(conn, "deleted")}
    assert deleted == {"gone0000000", "neverseen00"}

    stats = db.history_stats(conn)
    assert stats["deleted"] == 2
    assert stats["deleted_watched"] == 1


def test_readding_a_deleted_video_drops_the_snapshot(conn):
    make_video(conn, "back0000000", title="Back")
    db.record_play(conn, "back0000000")
    db.delete_video(conn, "back0000000")
    make_video(conn, "back0000000", title="Back")
    db.record_play(conn, "back0000000")
    items = db.watch_history(conn)
    assert len(items) == 1
    assert items[0]["deleted_at"] is None
    assert db.watch_history(conn, "deleted") == []


def test_history_filters(conn):
    make_video(conn, "finished000", duration_sec=100)
    make_video(conn, "halfway0000", duration_sec=1000)
    make_video(conn, "rewatched00", duration_sec=100)
    db.set_watch_state(conn, "finished000", status="watched", rating=1)
    db.set_resume_position(conn, "halfway0000", 400)
    db.record_play(conn, "rewatched00")
    db.record_play(conn, "rewatched00")

    def ids(f):
        return {i["video_id"] for i in db.watch_history(conn, f)}

    assert ids("all") == {"finished000", "halfway0000", "rewatched00"}
    assert ids("finished") == {"finished000"}
    assert ids("progress") == {"halfway0000"}
    assert ids("rated") == {"finished000"}
    assert ids("rewatched") == {"rewatched00"}

    stats = db.history_stats(conn)
    assert stats["finished"] == 1
    assert stats["finished_week"] == 1
    assert stats["in_progress"] == 1
    assert stats["thumbs_up"] == 1
    assert stats["watched_sec"] == 100 + 400
    assert stats["remaining_sec"] == (1000 - 400) + 100


def test_theme_durations_count_what_is_left(conn):
    make_video(conn, "done0000000", theme="Cooking", duration_sec=1800)
    make_video(conn, "half0000000", theme="Cooking", duration_sec=3600)
    make_video(conn, "fresh000000", theme="Cooking", duration_sec=1200)
    make_video(conn, "nolength000", theme="Cooking")
    db.set_watch_state(conn, "done0000000", status="watched")
    db.set_resume_position(conn, "half0000000", 600)
    conn.commit()

    (cooking,) = [t for t in db.list_themes(conn) if t["name"] == "Cooking"]
    assert cooking["total_count"] == 4
    assert cooking["watched_count"] == 1
    assert cooking["total_sec"] == 1800 + 3600 + 1200
    assert cooking["remaining_sec"] == (3600 - 600) + 1200
    assert cooking["unknown_duration"] == 1

    # the unwatched-only count changes; how long the theme is does not
    (filtered,) = [t for t in db.list_themes(conn, watched=False) if t["name"] == "Cooking"]
    assert filtered["video_count"] == 3
    assert filtered["remaining_sec"] == cooking["remaining_sec"]


def test_history_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("YTDLP_COOKIES_BROWSER", "")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        conn = client.app.state.db
        make_video(conn, "apivideo000", title="API", duration_sec=60)
        assert client.post("/videos/apivideo000/play").status_code == 200
        items = client.get("/history").json()["items"]
        assert [i["video_id"] for i in items] == ["apivideo000"]
        assert client.get("/history?filter=nope").status_code == 400
        assert client.get("/history/stats").json()["plays"] == 1
    get_settings.cache_clear()
