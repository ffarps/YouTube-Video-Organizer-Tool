"""File logging, because the desktop window has nowhere to print.

Run from a console, a traceback lands on screen and you can read it. Run as a
window there is no console at all: an exception in a request thread vanishes,
a download thread dies silently, and a hard crash in the WebView takes the
process with it leaving nothing behind. Everything here exists to make those
three cases leave a trace on disk instead.

The file rotates, and it is opened in append mode on purpose: a log that is
truncated at startup is empty exactly when you need it, because the first
thing you do after a crash is start the app again.
"""
from __future__ import annotations

import faulthandler
import io
import logging
import logging.handlers
import sys
import threading
from pathlib import Path
from typing import Optional

LOG_NAME = "watchlog.log"
CRASH_NAME = "watchlog-crash.log"
MAX_BYTES = 2_000_000
BACKUP_COUNT = 5
FORMAT = "%(asctime)s %(levelname)-7s %(threadName)-14s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False
_crash_file: Optional[io.TextIOBase] = None


def setup(log_dir: Path, console: Optional[bool] = None) -> Path:
    """Send everything to `log_dir/watchlog.log`. Safe to call twice.

    `console` defaults to "whenever there is a stderr to write to", which is
    the difference between `uvicorn` in a terminal and `pythonw` in a window.
    """
    global _configured

    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_NAME
    if _configured:
        return path

    formatter = logging.Formatter(FORMAT, DATE_FORMAT)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    if console is None:
        console = sys.stderr is not None
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

    _install_hooks(log_dir)
    _configured = True
    logging.getLogger("watchlog").info("logging to %s", path)
    return path


def _install_hooks(log_dir: Path) -> None:
    """Catch what normal logging calls never see."""
    global _crash_file
    log = logging.getLogger("watchlog.crash")

    previous_hook = sys.excepthook

    def excepthook(exc_type, exc, tb) -> None:
        log.critical("unhandled exception", exc_info=(exc_type, exc, tb))
        previous_hook(exc_type, exc, tb)

    sys.excepthook = excepthook

    def threadhook(args) -> None:
        # A download runs on its own thread; without this its traceback goes
        # to a stderr that may not exist, and all you see is a stuck spinner.
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "unknown"
        log.critical(
            "unhandled exception in thread %s",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = threadhook

    # A segfault in WebView2 or pythonnet kills the interpreter outright, so no
    # Python-level handler ever runs. faulthandler writes the C-level stack
    # from the signal handler itself, which needs a real file kept open.
    if _crash_file is None:
        try:
            _crash_file = open(log_dir / CRASH_NAME, "a", encoding="utf-8")
            faulthandler.enable(_crash_file)
        except OSError:  # logging must never be the reason the app won't start
            _crash_file = None


class _StreamToLog(io.TextIOBase):
    """A file-like object that turns writes into log records, line by line."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger = logger
        self._level = level
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer.rstrip())
        self._buffer = ""

    def writable(self) -> bool:
        return True


def capture_std_streams() -> None:
    """Point sys.stdout/stderr at the log when they don't exist.

    Under `pythonw.exe` both are None, and the first `print` anywhere in the
    process — ours, uvicorn's, a library's — raises AttributeError and takes
    the app down. This is the safety net for code that writes to stdout
    instead of logging.
    """
    if sys.stdout is None:
        sys.stdout = _StreamToLog(logging.getLogger("watchlog.stdout"), logging.INFO)
    if sys.stderr is None:
        sys.stderr = _StreamToLog(logging.getLogger("watchlog.stderr"), logging.ERROR)
