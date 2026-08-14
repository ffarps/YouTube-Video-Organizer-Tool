import faulthandler
import logging
import sys
import threading

import numpy as np
import pytest

from app import db, logs
from app.categorize.embeddings import to_blob


@pytest.fixture(scope="session")
def _session_log_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("watchlog-logs")


@pytest.fixture(autouse=True)
def _logs_out_of_the_repo(_session_log_dir, monkeypatch):
    """Starting the app configures logging, so without this every test run
    would leave a logs/ folder in the working tree."""
    monkeypatch.setenv("LOG_PATH", str(_session_log_dir))


@pytest.fixture
def logging_sandbox():
    """Let a test call logs.setup() without leaking handlers into the rest.

    logs.setup() is deliberately global and once-only — it owns the root
    logger, sys.excepthook and faulthandler — so anything a test does to it has
    to be unwound by hand.
    """
    root = logging.getLogger()
    saved = (root.handlers[:], root.level, sys.excepthook, threading.excepthook)
    logs._configured = False
    logs._crash_file = None
    yield
    for handler in root.handlers[:]:
        if handler not in saved[0]:
            root.removeHandler(handler)
            handler.close()
    root.handlers[:], root.level, sys.excepthook, threading.excepthook = saved
    if logs._crash_file is not None:
        faulthandler.disable()
        logs._crash_file.close()
    logs._configured = False
    logs._crash_file = None


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
