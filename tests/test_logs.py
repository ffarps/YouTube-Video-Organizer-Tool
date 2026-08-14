"""Logging is the only witness a console-less crash leaves behind."""
import logging
import sys
import threading

from app import logs


def test_setup_writes_to_a_rotating_file(tmp_path, logging_sandbox):
    path = logs.setup(tmp_path / "logs")

    logging.getLogger("watchlog.test").warning("something worth keeping")
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = path.read_text(encoding="utf-8")
    assert "something worth keeping" in text
    assert "WARNING" in text
    assert path.name == logs.LOG_NAME


def test_setup_appends_instead_of_truncating(tmp_path, logging_sandbox):
    """The first thing you do after a crash is start the app again — that must
    not erase the evidence."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / logs.LOG_NAME).write_text("previous run\n", encoding="utf-8")

    path = logs.setup(log_dir)
    logging.getLogger("watchlog.test").info("this run")
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = path.read_text(encoding="utf-8")
    assert "previous run" in text and "this run" in text


def test_setup_is_idempotent(tmp_path, logging_sandbox):
    before = len(logging.getLogger().handlers)
    logs.setup(tmp_path / "logs")
    after_first = len(logging.getLogger().handlers)
    logs.setup(tmp_path / "logs")

    assert after_first > before
    assert len(logging.getLogger().handlers) == after_first


def test_a_dying_thread_leaves_a_traceback(tmp_path, logging_sandbox):
    """A download runs on its own thread; its exception has to land somewhere."""
    path = logs.setup(tmp_path / "logs")

    def boom():
        raise ValueError("download blew up")

    thread = threading.Thread(target=boom, name="download-test")
    thread.start()
    thread.join()
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = path.read_text(encoding="utf-8")
    assert "download-test" in text
    assert "ValueError: download blew up" in text
    assert "Traceback" in text


def test_capture_std_streams_replaces_a_missing_stdout(tmp_path, logging_sandbox, monkeypatch):
    path = logs.setup(tmp_path / "logs")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    logs.capture_std_streams()
    print("printed, not lost")
    print("to stderr too", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = path.read_text(encoding="utf-8")
    assert "printed, not lost" in text
    assert "to stderr too" in text


def test_a_failing_request_is_logged_with_its_path(tmp_path, monkeypatch, logging_sandbox):
    """A 500 in the log is a puzzle without the request that caused it."""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "logs"))
    get_settings.cache_clear()

    app = create_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("kaboom")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/boom").status_code == 500

    for handler in logging.getLogger().handlers:
        handler.flush()
    text = (tmp_path / "logs" / logs.LOG_NAME).read_text(encoding="utf-8")
    get_settings.cache_clear()

    assert "GET /boom" in text
    assert "RuntimeError: kaboom" in text
    assert "Traceback" in text


def test_dump_stacks_names_every_thread(tmp_path, logging_sandbox):
    """A freeze raises nothing, so the stacks are the whole diagnosis."""
    path = logs.setup(tmp_path / "logs")
    started = threading.Event()
    release = threading.Event()

    def parked():
        started.set()
        release.wait(5)

    thread = threading.Thread(target=parked, name="parked-thread")
    thread.start()
    started.wait(5)
    try:
        text = logs.dump_stacks("test")
    finally:
        release.set()
        thread.join()

    assert "parked-thread" in text
    assert "MainThread" in text
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "parked-thread" in path.read_text(encoding="utf-8")


def test_capture_std_streams_leaves_real_streams_alone(tmp_path, logging_sandbox):
    logs.setup(tmp_path / "logs")
    before = sys.stdout

    logs.capture_std_streams()

    assert sys.stdout is before
