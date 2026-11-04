"""Validated Titles Sender: picks booking numbers off validated-title PDFs,
files them in SourceImages, emails them through Microsoft Graph and notes
the booking in VShip CRM.

Run from source with:  python main.py
See README.md for setup.
"""

import subprocess
import sys

# The windowed .exe has no console, so every child process (pdfinfo, pdftoppm,
# tesseract) would open a console window of its own. Patch Popen before
# pdf2image and pytesseract are imported so they use the quiet version.
if sys.platform == "win32":
    class _QuietPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _QuietPopen

import logging  # noqa: E402

from app_logging import setup_logging  # noqa: E402
from config import ConfigError, load_settings  # noqa: E402

APP_NAME = "Validated Titles Sender"

log = logging.getLogger("main")


def show_error(title: str, message: str) -> None:
    """Show an error dialog before (or without) the main window."""
    import tkinter as tk
    from tkinter import messagebox

    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message, parent=root)
        root.destroy()
    except tk.TclError:
        pass  # no display; the error is in the log


def main() -> int:
    log_path = setup_logging()
    log.debug("Starting %s", APP_NAME)

    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("%s", exc)
        show_error(f"{APP_NAME}: settings problem", str(exc))
        return 1

    try:
        from gui import SendPDFApp

        app = SendPDFApp(settings, log_path)
    except Exception as exc:
        log.exception("The app couldn't start")
        where = f"\n\nDetails are in the log file:\n{log_path}" if log_path else ""
        show_error(f"{APP_NAME} couldn't start", f"{exc}{where}")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
