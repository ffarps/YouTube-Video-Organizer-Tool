"""The desktop shell, minus the window.

Opening a real window needs a display, so these cover the parts that can go
wrong headlessly: port selection, the console-less stream fix, and the
server-thread lifecycle that closing the window depends on.
"""
import socket
import sys
import urllib.request

import pytest

from app import desktop
from app.config import get_settings


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_pick_port_keeps_the_preferred_one_when_free():
    port = _free_port()
    assert desktop._pick_port(port) == port


def test_pick_port_steps_aside_when_the_preferred_one_is_taken():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]

        port = desktop._pick_port(taken)
        assert port != taken
        # and the replacement is actually usable
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))


class _FakeWebview:
    """Just the bit of the pywebview module _window_geometry looks at."""

    class _Screen:
        def __init__(self, width, height):
            self.width, self.height = width, height

    def __init__(self, width=None, height=None):
        self.screens = [self._Screen(width, height)] if width else []


def test_window_geometry_fills_a_big_screen_and_centres():
    width, height, x, y = desktop._window_geometry(_FakeWebview(2048, 1152))
    assert (width, height) == (1800, 1036)  # 90%, capped at 1800 wide
    assert (x, y) == ((2048 - 1800) // 2, (1152 - 1036) // 2)


def test_window_geometry_still_fits_a_small_screen():
    width, height, _, _ = desktop._window_geometry(_FakeWebview(1366, 768))
    assert width <= 1366 and height <= 768


def test_window_geometry_falls_back_without_screen_info():
    assert desktop._window_geometry(_FakeWebview()) == (1360, 900, None, None)


def test_start_logging_survives_a_missing_stdout(tmp_path, monkeypatch, logging_sandbox):
    """pythonw.exe hands us sys.stdout = None; the first print anywhere in the
    process must not take it down."""
    monkeypatch.setenv("LOG_PATH", str(tmp_path / "logs"))
    get_settings.cache_clear()
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    path = desktop._start_logging()

    assert sys.stdout is not None
    print("hello from pythonw")
    sys.stdout.flush()
    assert "hello from pythonw" in path.read_text(encoding="utf-8")
    get_settings.cache_clear()


def test_wait_until_serving_reports_a_dead_server_thread():
    class Dead:
        started = False

        def is_alive(self):
            return False

    with pytest.raises(RuntimeError):
        desktop._wait_until_serving(Dead(), Dead(), timeout=5)


def test_server_thread_serves_then_stops(tmp_path, monkeypatch):
    """The window's whole contract: it starts, it answers, and closing it
    leaves nothing running."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "desktop.db"))
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path / "media"))
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("YTDLP_COOKIES_BROWSER", "")
    get_settings.cache_clear()

    port = _free_port()
    server, thread = desktop._start_server(port)
    try:
        desktop._wait_until_serving(server, thread, timeout=30)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/themes", timeout=10) as resp:
            assert resp.status == 200
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        get_settings.cache_clear()

    assert not thread.is_alive()
