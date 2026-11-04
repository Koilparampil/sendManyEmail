import re
import os
import sys

# When frozen by PyInstaller, _MEIPASS is the temp folder where bundled files land.
# When running from source, fall back to the directory containing this file.
_BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_TESSERACT_EXE = os.path.join(_BASE, "tesseract", "tesseract.exe")
_POPPLER_BIN   = os.path.join(_BASE, "poppler")

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

try:
    import pytesseract
    if os.path.isfile(_TESSERACT_EXE):
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_EXE
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

PREVIEW_AVAILABLE = PDF2IMAGE_AVAILABLE and PIL_AVAILABLE
OCR_AVAILABLE = PDF2IMAGE_AVAILABLE and TESSERACT_AVAILABLE and PIL_AVAILABLE

_BOOKING_PATTERNS = [
    # Carrier-specific fallbacks for docs without a BOOKING NO label
    r'(EBKG\d\s?\d{7})',  # allow one optional OCR-inserted space (e.g. "EBKG1 6646623")
    r'(NAM\d{7})',
    r'(S3\d{8})',
    # Primary: anchor on "BOOKING NO" label, grab first all-digit run (7-12 digits) after it.
    # [\s\S]{0,200}? crosses line breaks. Phone numbers are safe because OCR reads
    # them with dashes (917-569-1967), so they never form a continuous \d{7,12} run.
    r'BOOKING\s{0,3}NO[\s\S]{0,200}?(\d{7,12})',
]


def get_first_page_image(pdf_path: str):
    """Render first page of PDF at 600 DPI. Returns PIL Image or None."""
    if not PDF2IMAGE_AVAILABLE or not PIL_AVAILABLE:
        return None
    try:
        poppler_path = _POPPLER_BIN if os.path.isdir(_POPPLER_BIN) else None
        pages = convert_from_path(
            pdf_path, dpi=600, first_page=1, last_page=1,
            poppler_path=poppler_path,
        )
        return pages[0] if pages else None
    except Exception:
        return None


def get_pages_images(pdf_path: str, max_pages: int = 2, dpi: int = 600) -> list:
    """Render up to the first *max_pages* pages of a PDF for on-screen review.
    Returns a list of PIL Images (empty on failure)."""
    if not PDF2IMAGE_AVAILABLE or not PIL_AVAILABLE:
        return []
    try:
        poppler_path = _POPPLER_BIN if os.path.isdir(_POPPLER_BIN) else None
        return convert_from_path(
            pdf_path, dpi=dpi, first_page=1, last_page=max_pages,
            poppler_path=poppler_path,
        )
    except Exception:
        return []


def get_ocr_text(image) -> tuple[str, str]:
    """
    Run Tesseract OCR on a PIL Image using multiple PSM modes and combine results.
    PSM 6 = uniform block of text (good for prose).
    PSM 11 = sparse text (good for forms where values sit inside table cells).
    Returns (combined_text, error).
    """
    if not OCR_AVAILABLE or image is None:
        return "", "OCR dependencies not available"
    try:
        gray = image.convert("L")
        text = pytesseract.image_to_string(gray)
        return text, ""
    except Exception as exc:
        return "", str(exc)


def extract_booking_number(image) -> str:
    """Try to extract a booking/reference number from a PIL Image via OCR."""
    text, _ = get_ocr_text(image)
    if not text:
        return ""
    for pattern in _BOOKING_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).upper().replace(" ", "")
    return ""


def make_preview_photo(image, max_width: int = 360, max_height: int = 480):
    """Scale PIL Image to fit within max dimensions. Returns ImageTk.PhotoImage or None."""
    if not PIL_AVAILABLE or image is None:
        return None
    try:
        w, h = image.size
        scale = min(max_width / w, max_height / h)
        resized = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return ImageTk.PhotoImage(resized)
    except Exception:
        return None
