import numpy as np
import pytest

from app import db
from app.categorize.embeddings import to_blob


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(str(tmp_path / "test.db"))
    db.init_db(connection)
    yield connection
    connection.close()


def make_video(conn, video_id, title="video", theme=None, embedding=None, **kwargs):
    """Insert a video with optional theme (manual) and fake embedding vector."""
    db.upsert_video(conn, {"id": video_id, "title": title, **kwargs})
    if theme:
        theme_id = db.get_or_create_theme(conn, theme)
        db.assign_theme(conn, video_id, theme_id, 1.0, "manual")
    if embedding is not None:
        vector = np.asarray(embedding, dtype=np.float32)
        vector = vector / np.linalg.norm(vector)
        db.save_embedding(conn, video_id, to_blob(vector))
    conn.commit()
