"""Render page 1 of a PDF, OCR it, and find the booking number.

The page is rendered once, at 300 DPI in colour, and that render is used for
both the on-screen preview and OCR. PageAnalyzer caches the results per file,
so a PDF that was previewed isn't rendered or OCR'd again when it's sent.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

RENDER_DPI = 300

# When frozen by PyInstaller, _MEIPASS is the folder the bundled files are in.
# When running from source, it's the folder this file is in.
_BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_TESSERACT_EXE = os.path.join(_BASE, "tesseract", "tesseract.exe")
_POPPLER_BIN = os.path.join(_BASE, "poppler")

try:
    from pdf2image import convert_from_path

    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

try:
    import pytesseract

    if os.path.isfile(_TESSERACT_EXE):
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_EXE
    PYTESSERACT_AVAILABLE = True
except ImportError:
    PYTESSERACT_AVAILABLE = False

try:
    from PIL import Image

    PIL_AVAILABLE = True
    _LANCZOS = getattr(Image, "Resampling", Image).LANCZOS
except ImportError:
    PIL_AVAILABLE = False


class RenderError(Exception):
    """The PDF couldn't be rendered. The message says why."""


class OcrError(Exception):
    """OCR couldn't run. The message says why."""


# --- Booking numbers -----------------------------------------------------------

# Carrier formats, recognised anywhere on the page.
_CARRIER_FORMATS = (
    r"EBKG\d\s?\d{7}",  # one OCR-inserted space allowed, e.g. "EBKG1 6646623"
    r"NAM\d{7}",
    r"S3\d{8}",
)
# A booking number is never part of a longer token: no letter or digit right
# before it, and no digit right after it. So "XS312345678" doesn't yield
# "S312345678", and "NAM12345678" isn't cut down to "NAM1234567".
_CARRIER_RE = re.compile(rf"(?<![A-Z0-9])(?:{'|'.join(_CARRIER_FORMATS)})(?!\d)")
# After a BOOKING NO label the value can also be a plain 7-12 digit number.
_LABELLED_VALUE_RE = re.compile(rf"(?<![A-Z0-9])(?:{'|'.join(_CARRIER_FORMATS)}|\d{{7,12}})(?!\d)")
# "BOOKING NO", "BOOKING NO.", "BOOKING NO:", but not "BOOKING NOTES".
_LABEL_RE = re.compile(r"BOOKING\s{0,3}NO(?![A-Z])")
_LABEL_WINDOW = 200  # the value must start within this many characters of the label


def find_booking_number(text: str) -> str:
    """Return the booking number in OCR text, or "" if there isn't one.

    A value after a "BOOKING NO" label wins. Otherwise the first
    carrier-format number (EBKG…, NAM…, S3…) anywhere in the text is used.
    """
    upper = text.upper()
    for label in _LABEL_RE.finditer(upper):
        # Search a slice, so a value glued to the label ("BOOKING NO1234567")
        # still counts as starting on a boundary.
        match = _LABELLED_VALUE_RE.search(upper[label.end():])
        if match and match.start() < _LABEL_WINDOW:
            return _without_spaces(match.group())
    match = _CARRIER_RE.search(upper)
    return _without_spaces(match.group()) if match else ""


def _without_spaces(value: str) -> str:
    return "".join(value.split())


# --- Rendering and OCR -------------------------------------------------------------


def _poppler_path() -> str | None:
    """The bundled Poppler folder, or None to use Poppler from PATH."""
    return _POPPLER_BIN if os.path.isdir(_POPPLER_BIN) else None


def render_first_page(pdf_path: str, dpi: int = RENDER_DPI):
    """Render page 1 of the PDF in colour. Returns a PIL image; raises RenderError."""
    if not (PDF2IMAGE_AVAILABLE and PIL_AVAILABLE):
        raise RenderError("Showing and reading PDFs needs pdf2image and Pillow (pip install pdf2image pillow).")
    try:
        pages = convert_from_path(
            pdf_path, dpi=dpi, first_page=1, last_page=1, poppler_path=_poppler_path()
        )
    except Exception as exc:  # pdf2image raises several unrelated exception types
        raise RenderError(f"Couldn't render the PDF: {exc}".strip()) from exc
    if not pages:
        raise RenderError("The PDF has no pages.")
    return pages[0]


def ocr_text(image) -> str:
    """Return Tesseract's text for a greyscale copy of image.

    Uses Tesseract's default page segmentation. Raises OcrError.
    """
    if not PYTESSERACT_AVAILABLE:
        raise OcrError("OCR needs pytesseract (pip install pytesseract) and Tesseract.")
    try:
        return pytesseract.image_to_string(image.convert("L"))
    except Exception as exc:
        raise OcrError(f"Tesseract failed: {exc}".strip()) from exc


def fit_image(image, max_width: int, max_height: int):
    """Return a copy of image scaled to fit within max_width x max_height."""
    width, height = image.size
    scale = min(max_width / width, max_height / height)
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(size, _LANCZOS)


@dataclass(frozen=True)
class OcrTools:
    """What check_ocr_tools() found."""

    can_render: bool
    can_ocr: bool
    problems: tuple[str, ...] = ()
    tesseract_version: str = ""


def check_ocr_tools() -> OcrTools:
    """Check that the Python packages *and* the Poppler and Tesseract programs work."""
    problems: list[str] = []
    if not PIL_AVAILABLE:
        problems.append("Pillow isn't installed.")
    if not PDF2IMAGE_AVAILABLE:
        problems.append("pdf2image isn't installed.")

    folder = _poppler_path()
    poppler_ok = all(shutil.which(tool, path=folder) for tool in ("pdftoppm", "pdfinfo"))
    if not poppler_ok:
        where = f"in {folder}" if folder else "in the poppler folder or on PATH"
        problems.append(f"Poppler (pdftoppm and pdfinfo) wasn't found {where}.")

    version = ""
    if not PYTESSERACT_AVAILABLE:
        problems.append("pytesseract isn't installed.")
    else:
        try:
            version = str(pytesseract.get_tesseract_version())
        except Exception as exc:
            problems.append(f"Tesseract OCR wasn't found or didn't run ({exc}).")

    can_render = PIL_AVAILABLE and PDF2IMAGE_AVAILABLE and poppler_ok
    return OcrTools(
        can_render=can_render,
        can_ocr=can_render and bool(version),
        problems=tuple(problems),
        tesseract_version=version,
    )


# --- Cached analysis ---------------------------------------------------------------


@dataclass(frozen=True)
class PageReading:
    """What OCR made of page 1 of a PDF."""

    text: str = ""
    booking: str = ""
    error: str = ""  # why the page couldn't be read; "" when OCR ran


PreviewCallback = Callable[[object, str], None]  # (preview image or None, error)


class PageAnalyzer:
    """Renders page 1 once and reuses that render for the preview and OCR.

    OCR readings are kept for every file seen (they're small); preview images
    only for the most recently used files. Results are keyed by the file's
    path, size and modification time, so a changed file is read again. Safe
    to use from several threads: one file is never rendered by two at once.
    """

    def __init__(
        self,
        preview_size: tuple[int, int] = (1000, 1300),
        max_previews: int = 20,
        *,
        render: Callable[[str], object] = render_first_page,
        ocr: Callable[[object], str] = ocr_text,
    ):
        self.preview_size = preview_size
        self.max_previews = max_previews
        self._render = render
        self._ocr = ocr
        self._lock = threading.Lock()
        self._file_locks: dict[tuple, threading.Lock] = {}
        self._readings: dict[tuple, PageReading] = {}
        self._previews: OrderedDict[tuple, object] = OrderedDict()

    def read(
        self,
        pdf_path: str,
        *,
        on_preview: PreviewCallback | None = None,
        keep_going: Callable[[], bool] = lambda: True,
    ) -> PageReading | None:
        """Return the OCR reading of page 1, rendering the page only if needed.

        on_preview, if given, is called with (preview, error) as soon as a
        preview is available (straight away when one is cached), before OCR
        runs. If keep_going() is False at that point, OCR is skipped and None
        is returned; the send step will do it later.
        """
        try:
            stat = os.stat(pdf_path)
        except OSError as exc:
            error = f"Can't open the PDF: {exc.strerror or exc}"
            if on_preview is not None:
                on_preview(None, error)
            return PageReading(error=error)
        key = (os.path.normcase(os.path.abspath(pdf_path)), stat.st_size, stat.st_mtime_ns)

        with self._lock_for(key):
            reading = self._readings.get(key)
            need_preview = False
            if on_preview is not None:
                preview = self._cached_preview(key)
                if preview is not None:
                    on_preview(preview, "")
                else:
                    need_preview = True
            if reading is not None and not need_preview:
                return reading
            if reading is None and not need_preview and not keep_going():
                return None

            try:
                page = self._render(pdf_path)
            except RenderError as exc:
                if need_preview:
                    on_preview(None, str(exc))
                if reading is None:
                    reading = PageReading(error=str(exc))
                    self._readings[key] = reading
                return reading

            if need_preview:
                preview = fit_image(page, *self.preview_size)
                self._store_preview(key, preview)
                on_preview(preview, "")
            if reading is None:
                if not keep_going():
                    return None
                reading = self._read_text(page)
                self._readings[key] = reading
            return reading

    def _read_text(self, page) -> PageReading:
        try:
            text = self._ocr(page)
        except OcrError as exc:
            return PageReading(error=str(exc))
        return PageReading(text=text, booking=find_booking_number(text))

    def _lock_for(self, key: tuple) -> threading.Lock:
        with self._lock:
            return self._file_locks.setdefault(key, threading.Lock())

    def _cached_preview(self, key: tuple):
        with self._lock:
            preview = self._previews.get(key)
            if preview is not None:
                self._previews.move_to_end(key)
            return preview

    def _store_preview(self, key: tuple, preview) -> None:
        with self._lock:
            self._previews[key] = preview
            self._previews.move_to_end(key)
            while len(self._previews) > self.max_previews:
                self._previews.popitem(last=False)
