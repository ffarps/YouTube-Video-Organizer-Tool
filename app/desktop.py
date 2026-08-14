"""Desktop shell: the app in its own window, with no browser tab and no console.

Nothing about the app changes here — this module is only a front door. Uvicorn
runs on a background thread bound to loopback and the main thread owns the
window, because a GUI event loop has to live on thread 0. Closing the window
stops the server and ends the process, so there is never a stray server left
behind for the user to hunt down.

Started with `pythonw.exe` (see `Watchlog.vbs` and `start.bat`) there is no
console at all, and therefore no stdout: `_redirect_streams` has to point the
streams at a log file before anything writes to them, and failures have to be
reported with a message box instead of a traceback nobody would ever see.

The window itself is a WebView2 surface via pywebview. Without that package
installed we fall back to a Chromium browser in `--app` mode, which is the same
chromeless window by other means; both paths block until the user closes it.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "watchlog-desktop.log"
DEFAULT_PORT = 8000
TITLE = "My Watch Log"
# Matches --bg of the app's default (dark) theme, so opening the window doesn't
# flash white before the page paints.
BACKGROUND = "#0f1115"


def _redirect_streams() -> None:
    """Give the process somewhere to print.

    `pythonw.exe` leaves `sys.stdout` and `sys.stderr` as None, and the first
    line uvicorn logs would take the whole app down with an AttributeError.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        stream = open(LOG_PATH, "w", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _data_dir(name: str) -> Path:
    """Per-user directory for window state that isn't app data.

    Kept out of the project folder: it's browser cache, not something you'd
    ever back up next to organizer.db.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    path = base / "Watchlog" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pick_port(preferred: int = DEFAULT_PORT) -> int:
    """Keep :8000 when it is free — the README, /docs and any bookmark assume
    it — and take whatever the OS hands out when something already has it."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("No free TCP port on localhost")


def _start_server(port: int):
    """Run the FastAPI app on a daemon thread. Returns (server, thread)."""
    import uvicorn

    from app.main import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    # Daemon: if the GUI dies badly, the process still exits.
    thread = threading.Thread(target=server.run, name="watchlog-server", daemon=True)
    thread.start()
    return server, thread


def _wait_until_serving(server, thread: threading.Thread, timeout: float = 60.0) -> None:
    """Block until uvicorn accepts connections, or explain why it never will.

    The window must not open on a URL that isn't listening yet — WebView2 would
    render its own "can't reach this page" and stay there.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return
        if not thread.is_alive():
            raise RuntimeError(
                "The server thread stopped during startup. "
                "Is another copy of Watchlog already using the port?"
            )
        time.sleep(0.05)
    raise RuntimeError(f"The server did not start within {timeout:.0f}s")


def _window_geometry(webview) -> tuple:
    """Most of the screen, centred. Returns (width, height, x, y).

    The Browse grid lays out in fixed-width columns and simply clips the last
    one, so a window that merely 'fits' wastes a column on a big monitor; the
    ceiling stops it spanning an ultrawide edge to edge. x/y are set because
    leaving them to the toolkit gets the OS cascade — every launch a few
    pixels further down the screen than the last.
    """
    try:
        screen = webview.screens[0]
        width = min(1800, max(1024, int(screen.width * 0.9)))
        height = min(1150, max(700, int(screen.height * 0.9)))
        return width, height, (screen.width - width) // 2, (screen.height - height) // 2
    except Exception:  # no display info — headless, or an unusual GUI backend
        return 1360, 900, None, None


def _open_window(url: str) -> bool:
    """Native window via pywebview. False if the package isn't installed."""
    try:
        import webview
    except ImportError:
        return False

    # "open on YouTube" and channel links should leave the app window and land
    # in the real browser, where the user is signed in.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    width, height, x, y = _window_geometry(webview)
    window = webview.create_window(
        TITLE,
        url,
        width=width,
        height=height,
        x=x,
        y=y,
        min_size=(920, 620),
        background_color=BACKGROUND,
        text_select=True,
    )

    def reveal() -> None:
        """Put the window on screen whatever state it opened in.

        Windows hands a new process a show state and applies it to the first
        window that process opens: launched from a script host it can arrive
        hidden or minimized, which is indistinguishable from a crash except
        that the server is up and only Task Manager can stop it. Doing this
        unconditionally is harmless on a window that is already visible.
        """
        window.show()
        window.restore()

    # private_mode=False + storage_path: the colour theme lives in
    # localStorage, and pywebview throws that away between runs by default.
    webview.start(
        reveal,
        private_mode=False,
        storage_path=str(_data_dir("webview")),
    )
    return True


def _chromium_exe() -> Optional[str]:
    """A Chromium-family browser that understands --app, or None."""
    names = ["msedge", "chrome", "chromium", "brave", "vivaldi"]
    if sys.platform != "win32":
        names = ["google-chrome", "chromium-browser", "microsoft-edge"] + names
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    candidates: List[Path] = []
    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for root in filter(None, roots):
            candidates += [
                Path(root) / "Microsoft/Edge/Application/msedge.exe",
                Path(root) / "Google/Chrome/Application/chrome.exe",
            ]
    elif sys.platform == "darwin":
        candidates += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _open_browser_window(url: str, port: int) -> bool:
    """Fallback window: Chromium in --app mode, chromeless and title-bar only.

    The dedicated --user-data-dir is what makes this usable as a shell: it
    forces a browser process we own (so closing the window returns from
    `wait()` and shuts the server down) and it keeps the theme setting across
    runs instead of borrowing the user's everyday profile. It is per-port
    because a second instance pointed at the same profile would be handed off
    to the first browser process and exit immediately, taking its own server
    down while its window stayed open.
    """
    exe = _chromium_exe()
    if not exe:
        return False
    proc = subprocess.Popen(
        [
            exe,
            f"--app={url}",
            f"--user-data-dir={_data_dir(f'browser-profile-{port}')}",
            "--window-size=1360,900",
            "--no-first-run",
            "--no-default-browser-check",
        ]
    )
    proc.wait()
    return True


def _error_box(message: str) -> None:
    """Report a startup failure. With no console, this is the only channel."""
    print(message, file=sys.stderr)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)  # MB_ICONERROR


def run(port: Optional[int] = None) -> int:
    _redirect_streams()
    if port is None:
        env_port = os.environ.get("WATCHLOG_PORT")
        port = int(env_port) if env_port else _pick_port()

    server, thread = _start_server(port)
    url = f"http://127.0.0.1:{port}/"
    try:
        _wait_until_serving(server, thread)
    except Exception as exc:
        _error_box(f"Watchlog could not start.\n\n{exc}\n\nLog: {LOG_PATH}")
        return 1

    try:
        if not (_open_window(url) or _open_browser_window(url, port)):
            _error_box(
                "Watchlog is running but there is no window to show it in.\n\n"
                'Install the desktop dependency:  pip install -e ".[desktop]"\n'
                f"or open {url} in your browser (start.bat browser).\n\n"
                f"Log: {LOG_PATH}"
            )
            return 1
    finally:
        # Window closed (or never opened): take the server down with it.
        server.should_exit = True
        thread.join(timeout=5)
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
