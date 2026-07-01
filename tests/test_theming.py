import numpy as np

from app import db
from app.categorize import embeddings, themes
from tests.conftest import make_video

# Orthogonal directions for two "topics"
GUITAR = [1, 0, 0, 0]
AI = [0, 1, 0, 0]
NEAR_GUITAR = [0.9, 0.1, 0, 0]
NOWHERE = [0, 0, 0.7, 0.7]


def seed_members(conn):
    make_video(conn, "guitarseed1", "g1", theme="Guitar", embedding=GUITAR)
    make_video(conn, "guitarseed2", "g2", theme="Guitar", embedding=[0.95, 0.05, 0, 0])
    make_video(conn, "aiseed00001", "a1", theme="AI", embedding=AI)


def test_prototypes(conn):
    seed_members(conn)
    prototypes = themes.theme_prototypes(conn)
    assert set(prototypes) == {"Guitar", "AI"}
    assert np.isclose(np.linalg.norm(prototypes["Guitar"]), 1.0, atol=1e-5)
    # Guitar prototype points along the guitar axis
    assert prototypes["Guitar"][0] > 0.9


def test_auto_assign_and_review(conn):
    seed_members(conn)
    make_video(conn, "nearguitar1", "unlabeled", embedding=NEAR_GUITAR)
    make_video(conn, "nowherevid1", "weird", embedding=NOWHERE)

    result = themes.auto_assign(conn, threshold=0.45)
    assert result["assigned"] == 1
    assert result["needs_review"] == 1

    video = db.get_video(conn, "nearguitar1")
    assert video["themes"] == ["Guitar"]

    queue = themes.review_queue(conn)
    ids = [item["video"]["id"] for item in queue]
    assert ids == ["nowherevid1"]
    # suggestions still ranked, just under threshold
    assert queue[0]["suggestions"][0]["theme"] in ("Guitar", "AI")


def test_auto_assign_without_prototypes(conn):
    make_video(conn, "lonelyvid01", "x", embedding=NOWHERE)
    result = themes.auto_assign(conn)
    assert result["assigned"] == 0


def test_build_embeddings_with_fake_model(conn, monkeypatch):
    make_video(conn, "noembed0001", "needs embedding")

    def fake_embed(texts):
        return np.tile(np.asarray([1, 0, 0, 0], dtype=np.float32), (len(texts), 1))

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    monkeypatch.setattr(themes.embeddings, "embed_texts", fake_embed)

    result = themes.build_embeddings(conn)
    assert result["embedded_now"] == 1
    assert result["remaining"] == 0
    assert db.embedding_counts(conn)["embedded"] == 1


def test_video_text_composition():
    text = embeddings.video_text(
        {
            "title": "T",
            "channel_title": "C",
            "tags": ["x", "y"],
            "description": "D" * 1000,
        }
    )
    assert text.startswith("T | C | x y | D")
    assert len(text) < 600  # description truncated
