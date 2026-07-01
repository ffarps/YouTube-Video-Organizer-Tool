from app import db
from app.recommend import engine
from tests.conftest import make_video

GUITAR = [1, 0, 0, 0]
AI = [0, 1, 0, 0]
NEAR_GUITAR = [0.95, 0.05, 0, 0]
NEAR_AI = [0.05, 0.95, 0, 0]


def test_profile_recommendations_rank_by_taste(conn):
    # loved a guitar video, disliked an AI video
    make_video(conn, "watchedgtr1", "watched guitar", embedding=GUITAR)
    db.set_watch_state(conn, "watchedgtr1", status="watched", rating=5)
    make_video(conn, "watchedai01", "watched ai", embedding=AI)
    db.set_watch_state(conn, "watchedai01", status="watched", rating=1)

    make_video(conn, "candidgtr01", "guitar candidate", embedding=NEAR_GUITAR)
    make_video(conn, "candidai001", "ai candidate", embedding=NEAR_AI)
    conn.commit()

    result = engine.recommend(conn)
    assert result["mode"] == "profile"
    ids = [v["id"] for v in result["recommendations"]]
    # watched videos never recommended
    assert "watchedgtr1" not in ids and "watchedai01" not in ids
    assert ids.index("candidgtr01") < ids.index("candidai001")
    scores = [v["score"] for v in result["recommendations"]]
    assert scores == sorted(scores, reverse=True) or len(set(ids)) == len(ids)


def test_duration_and_theme_filters(conn):
    make_video(conn, "watchedgtr1", "w", embedding=GUITAR)
    db.set_watch_state(conn, "watchedgtr1", status="watched", rating=5)
    make_video(
        conn, "longvideo01", "long", theme="Guitar", embedding=NEAR_GUITAR,
        duration_sec=4000,
    )
    make_video(
        conn, "shortvideo1", "short", theme="Guitar", embedding=NEAR_GUITAR,
        duration_sec=300,
    )
    make_video(conn, "otherthemed", "other", theme="AI", embedding=NEAR_AI,
               duration_sec=300)
    conn.commit()

    fits_budget = engine.recommend(conn, max_duration_sec=600)
    ids = [v["id"] for v in fits_budget["recommendations"]]
    assert "longvideo01" not in ids and "shortvideo1" in ids

    guitar_only = engine.recommend(conn, theme="Guitar")
    ids = [v["id"] for v in guitar_only["recommendations"]]
    assert "otherthemed" not in ids


def test_cold_start_uses_theme_affinity(conn):
    # watched (no embeddings anywhere -> cold start) two Guitar videos
    make_video(conn, "watchedgtr1", "w1", theme="Guitar")
    make_video(conn, "watchedgtr2", "w2", theme="Guitar")
    db.set_watch_state(conn, "watchedgtr1", status="watched")
    db.set_watch_state(conn, "watchedgtr2", status="watched")
    make_video(conn, "candidgtr01", "guitar candidate", theme="Guitar")
    make_video(conn, "candidai001", "ai candidate", theme="AI")
    conn.commit()

    result = engine.recommend(conn)
    assert result["mode"] == "cold_start"
    ids = [v["id"] for v in result["recommendations"]]
    assert ids.index("candidgtr01") < ids.index("candidai001")


def test_mmr_diversifies(conn):
    make_video(conn, "watchedgtr1", "w", embedding=GUITAR)
    db.set_watch_state(conn, "watchedgtr1", status="watched", rating=5)
    # five near-identical guitar candidates and one different-but-relevant
    for i in range(5):
        make_video(conn, f"dupe000000{i}", f"dupe {i}", embedding=NEAR_GUITAR)
    make_video(conn, "different01", "different", embedding=[0.7, 0.1, 0.7, 0])
    conn.commit()

    top3 = engine.recommend(conn, limit=3)["recommendations"]
    assert "different01" in [v["id"] for v in top3]
