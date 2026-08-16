"""Desktop shell: the app in its own window, with no browser tab and no console.

Nothing about the app changes here — this module is only a front door. Uvicorn
runs on a background thread bound to loopback and the main thread owns the
window, because a GUI event loop has to live on thread 0. Closing the window
stops the server and ends the process, so there is never a stray server left
behind for the user to hunt down.

Started with `pythonw.exe` (see `Watchlog.vbs` and `start.bat`) there is no
console at all, and therefore no stdout: logging has to be running before
anything writes a line, and failures have to be reported with a message box
instead of a traceback nobody would ever see. See `app/logs.py`.

The window itself is a WebView2 surface via pywebview. Without that package
installed we fall back to a Chromium browser in `--app` mode, which is the same
chromeless window by other means; both paths block until the user closes it.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from app import logs
from app.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8000
TITLE = "My Watch Log"

log = logging.getLogger("watchlog.desktop")
# Matches --bg of the app's default (dark) theme, so opening the window doesn't
# flash white before the page paints.
BACKGROUND = "#0f1115"


def _start_logging() -> Path:
    """Get logging running before anything else can fail.

    This is the first thing `run` does, because everything after it — the
    server thread, the window, the crash handler — is only debuggable if its
    output has somewhere to go.
    """
    log_dir = get_settings().log_dir()
    path = logs.setup(log_dir)
    # pythonw.exe leaves sys.stdout and sys.stderr as None, and the first
    # print anywhere in the process would take the app down with an
    # AttributeError before a single line got written.
    logs.capture_std_streams()
    return path


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


def _asset_stamp() -> str:
    """A cache-buster that changes when the frontend does.

    The window keeps its browser profile between runs, and a page cached
    without a freshness directive can be served for hours without ever asking
    the server (see the `Cache-Control` on `/` in app/main.py) — so an updated
    frontend silently doesn't appear, restart after restart, and the app looks
    exactly like it did before the change. A URL carrying index.html's mtime
    can't be answered out of that cache. Belt to the header's braces: the
    header only helps once the new page has been fetched at least once, which
    is the thing a stale cache prevents.
    """
    try:
        return str(int((ROOT / "static" / "index.html").stat().st_mtime))
    except OSError:  # no frontend on disk — the API still serves
        return ""


def _start_server(port: int):
    """Run the FastAPI app on a daemon thread. Returns (server, thread)."""
    import uvicorn

    from app.main import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        # On in the window, where it is the only record of what the UI asked
        # for. After a freeze the last few lines are the most useful thing in
        # the file: they say what the page was doing when it stopped.
        access_log=True,
        # log_config=None: don't let uvicorn install its own console handlers.
        # Its loggers then propagate to the root logger, so "Exception in ASGI
        # application" ends up in the same file as everything else instead of
        # on a stdout that doesn't exist.
        log_config=None,
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


def _main_window_handle() -> Optional[int]:
    """The largest visible top-level window this process owns, or None."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ours = os.getpid()
    best = [None, 0]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == ours and user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if area > best[1]:
                best[0], best[1] = hwnd, area
        return True

    user32.EnumWindows(visit, 0)
    return best[0]


def _start_watchdog(interval: float = 10.0, heartbeat: float = 300.0) -> None:
    """Notice a frozen window and write down what the process was doing.

    A hang leaves no traceback: nothing raises, the message pump simply stops
    and the window ignores even Alt+F4. Windows already tracks this — the same
    check that puts "(Not Responding)" in a title bar — so the only missing
    piece is a thread on the outside asking, and recording the answer while it
    is still true. The heartbeat gives the log a timeline, so a gap in it says
    when things stopped even if the freeze took the whole process down.
    """
    if sys.platform != "win32":
        return
    import ctypes

    def loop() -> None:
        hung_since: Optional[float] = None
        last_beat = 0.0
        while True:
            time.sleep(interval)
            try:
                hwnd = _main_window_handle()
                now = time.monotonic()
                if hwnd and ctypes.windll.user32.IsHungAppWindow(hwnd):
                    if hung_since is None:
                        hung_since = now
                        log.warning("the window has stopped responding to input")
                        logs.dump_stacks("window not responding")
                    elif now - hung_since > 60 and int(now - hung_since) % 60 < interval:
                        log.warning("still frozen after %.0fs", now - hung_since)
                elif hung_since is not None:
                    log.warning("window recovered after %.0fs", now - hung_since)
                    hung_since = None
                if now - last_beat >= heartbeat:
                    last_beat = now
                    log.info("alive: threads=%d", threading.active_count())
            except Exception:  # a watchdog must never be the thing that breaks
                log.exception("watchdog check failed")

    threading.Thread(target=loop, name="watchdog", daemon=True).start()


def _user32():
    """user32 with the signatures ctypes gets wrong left to itself.

    Handles and HMONITORs are pointer-sized; the default int conversion would
    truncate them to 32 bits and hand Windows a handle to nothing.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [
        wintypes.UINT
    ]
    return user32


def _set_topmost(hwnd: int, on: bool) -> None:
    """Move the window in or out of the always-on-top band, and nothing else.

    Position, size and style are left exactly as they are: while fullscreen
    this is the only thing that changes as focus comes and goes.
    """
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
    _user32().SetWindowPos(
        hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    )


def _fullscreen_win32(on: bool, saved: dict) -> None:
    """Cover the monitor — taskbar included — or put the window back.

    Two things have to be true before Windows lets a window over the taskbar,
    and "maximize it" gets neither: maximizing sizes a window to the *work
    area*, which is the screen minus the taskbar, and leaves it in the ordinary
    z-order band the always-on-top taskbar sits above. So the window is placed
    on the monitor's full rect and lifted into the topmost band instead, which
    is what every video player does. `saved` carries the style and placement to
    come back to; `SetWindowPlacement` restores a maximized window as maximized.
    """
    import ctypes
    from ctypes import wintypes

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.UINT),
            ("flags", wintypes.UINT),
            ("showCmd", wintypes.UINT),
            ("ptMinPosition", wintypes.POINT),
            ("ptMaxPosition", wintypes.POINT),
            ("rcNormalPosition", wintypes.RECT),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),  # the whole screen
            ("rcWork", wintypes.RECT),  # ...minus the taskbar
            ("dwFlags", wintypes.DWORD),
        ]

    GWL_STYLE, WS_OVERLAPPEDWINDOW = -16, 0x00CF0000
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x10, 0x20
    MONITOR_DEFAULTTONEAREST = 2

    user32 = _user32()

    if not on:
        hwnd = saved.pop("hwnd", None)
        if not hwnd:  # never entered fullscreen — nothing to undo
            return
        user32.SetWindowLongW(hwnd, GWL_STYLE, saved["style"])
        user32.SetWindowPos(
            hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED | SWP_NOACTIVATE,
        )
        user32.SetWindowPlacement(hwnd, ctypes.byref(saved["placement"]))
        return

    hwnd = _main_window_handle()
    if not hwnd:
        raise RuntimeError("no visible window to make fullscreen")

    placement = WINDOWPLACEMENT()
    placement.length = ctypes.sizeof(placement)
    user32.GetWindowPlacement(hwnd, ctypes.byref(placement))
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    saved.update(hwnd=hwnd, placement=placement, style=style)

    # Title bar and resize frame off: the client area, and so the webview, is
    # then the whole screen and the page's fullscreen element fills it.
    user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_OVERLAPPEDWINDOW)
    rect = info.rcMonitor
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST,
        rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top,
        SWP_FRAMECHANGED | SWP_NOACTIVATE,
    )


def _bind_fullscreen(window) -> None:
    """Let a fullscreen video take the screen, not just the window.

    WebView2 has no fullscreen of its own: `requestFullscreen()` inside it
    expands the element to fill the *webview*, and the webview stops at the
    window's edge — so a "fullscreen" video keeps the title bar, and the
    desktop stays visible around a window that is only 90% of the screen.
    Nothing in the page can fix that; only the process owning the window can.
    So the page reports every fullscreenchange here (`syncShellFullscreen` in
    static/index.html) and the window follows it.

    On Windows the move is made directly (`_fullscreen_win32`) rather than
    through pywebview's `toggle_fullscreen`, which maximizes the form and so
    leaves the taskbar drawn over the video. Elsewhere its version is the only
    one there is.

    Not needed in the Chromium fallback or in a real browser, where fullscreen
    already means the screen: `window.pywebview` doesn't exist there and the
    page skips the call.
    """
    state = {"on": False}
    saved: dict = {}
    watcher: dict = {"stop": None}
    lock = threading.Lock()

    def follow_focus(hwnd: int, stop: threading.Event, interval: float = 0.25) -> None:
        """Stay on top only while the window is the one in front.

        Being topmost is what puts a fullscreen video over the taskbar, but it
        outstays its welcome the moment you alt-tab: the window would sit over
        whatever you switched to. Nothing reports a lost activation to this
        process — the form's own events are pywebview's, not ours — so the
        foreground window is polled instead, the same tactic the watchdog uses
        for hangs. Only the z-order moves, so switching back finds the video
        still fullscreen.
        """
        user32 = _user32()
        on_top = True
        while not stop.wait(interval):
            try:
                front = user32.GetForegroundWindow() == hwnd
                if front == on_top:
                    continue
                with lock:
                    if not state["on"]:  # left fullscreen while we waited
                        return
                    _set_topmost(hwnd, front)
                on_top = front
            except Exception:
                log.exception("fullscreen focus watch failed")
                return

    def set_fullscreen(on: bool) -> bool:
        """Exposed to the page as `pywebview.api.set_fullscreen(bool)`."""
        want = bool(on)
        # Called on a worker thread (pywebview runs every exposed function on
        # its own), so two fullscreenchanges in quick succession can overlap.
        with lock:
            if want == state["on"]:
                return state["on"]
            if watcher["stop"]:  # stop before the geometry goes back
                watcher["stop"].set()
                watcher["stop"] = None
            try:
                if sys.platform == "win32":
                    _fullscreen_win32(want, saved)
                else:
                    window.toggle_fullscreen()
            except Exception:
                log.exception("could not %s fullscreen", "enter" if want else "leave")
                return state["on"]
            state["on"] = want
            log.info("window fullscreen: %s", want)
            if want and sys.platform == "win32":
                stop = threading.Event()
                watcher["stop"] = stop
                threading.Thread(
                    target=follow_focus,
                    args=(saved["hwnd"], stop),
                    name="fullscreen-focus",
                    daemon=True,
                ).start()
            return want

    window.expose(set_fullscreen)


def _open_window(url: str) -> bool:
    """Native window via pywebview. False if the package isn't installed."""
    try:
        import webview
    except ImportError:
        log.warning("pywebview not installed, falling back to a Chromium window")
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
    _bind_fullscreen(window)

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

    log.info("window backend: pywebview (fullscreen bridge active)")
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
    log.error(message.replace("\n", " "))
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)  # MB_ICONERROR


def run(port: Optional[int] = None) -> int:
    log_path = _start_logging()
    if port is None:
        env_port = os.environ.get("WATCHLOG_PORT")
        port = int(env_port) if env_port else _pick_port()

    log.info("desktop start: port=%s python=%s", port, sys.executable)
    _start_watchdog()
    server, thread = _start_server(port)
    stamp = _asset_stamp()
    url = f"http://127.0.0.1:{port}/" + (f"?v={stamp}" if stamp else "")
    try:
        _wait_until_serving(server, thread)
    except Exception as exc:
        log.exception("server did not come up")
        _error_box(f"Watchlog could not start.\n\n{exc}\n\nLog: {log_path}")
        return 1

    try:
        # Which backend opened the window decides what the page can do — the
        # fullscreen bridge only exists under pywebview — so say so in the log.
        log.info("opening window: trying pywebview")
        if not (_open_window(url) or _open_browser_window(url, port)):
            _error_box(
                "Watchlog is running but there is no window to show it in.\n\n"
                'Install the desktop dependency:  pip install -e ".[desktop]"\n'
                f"or open {url} in your browser (start.bat browser).\n\n"
                f"Log: {log_path}"
            )
            return 1
    except Exception as exc:  # the window itself failed, not the app
        log.exception("window backend crashed")
        _error_box(f"The Watchlog window closed unexpectedly.\n\n{exc}\n\nLog: {log_path}")
        return 1
    finally:
        # Window closed (or never opened): take the server down with it.
        log.info("window closed, stopping server")
        server.should_exit = True
        thread.join(timeout=5)
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
