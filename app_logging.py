"""Logging for the app.

Everything goes to a rotating log file in
%LOCALAPPDATA%\\ValidatedTitlesSender\\logs\\app.log, including tracebacks
and the raw OCR text of PDFs whose booking number wasn't found. The main
window adds its own handler to show INFO and above on screen.

The windowed .exe has no console (sys.stdout and sys.stderr are None), so
print() output and uncaught exceptions would otherwise disappear. The hooks
installed here write those exceptions to the log file instead.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading

APP_NAME = "ValidatedTitlesSender"

_FILE_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_NOISY_LIBRARIES = ("urllib3", "msal", "PIL", "pdf2image", "pytesseract")

log = logging.getLogger(__name__)
_configured_path: str | None = None
_configured = False


def log_directory() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, APP_NAME, "logs")


def setup_logging() -> str | None:
    """Configure logging once. Returns the log file's path, or None if it couldn't be created."""
    global _configured, _configured_path
    if _configured:
        return _configured_path
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)

    try:
        directory = log_directory()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "app.log")
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(handler)
        _configured_path = path
    except OSError:
        _configured_path = None

    if sys.stderr is not None:  # running from source in a terminal
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console)

    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread
    return _configured_path


def _log_uncaught(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    log.critical("Unhandled error: %s", exc, exc_info=(exc_type, exc, tb))


def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else "?"
    log.critical(
        "Unhandled error in thread %s: %s",
        name,
        args.exc_value,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
