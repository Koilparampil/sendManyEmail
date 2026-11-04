"""
Send PDFs via Email using Microsoft Graph API

Reads Azure credentials from a .env file and provides a tkinter GUI
to select PDF files, preview pages, OCR booking numbers, and send to
multiple recipients.

Required:
    pip install msal requests python-dotenv pdf2image pillow pytesseract
    + Poppler (for pdf2image)  https://github.com/oschwartz10612/poppler-windows/releases
    + Tesseract (for OCR)      https://github.com/UB-Mannheim/tesseract/wiki

.env file format:
    APP_CLIENT_ID=your-client-id
    DIRECTORY_TENANT_ID=your-tenant-id
    VALUE=your-client-secret-value
    SECRET_ID=your-secret-id          (optional)
    SENDER_EMAIL=sender@domain.com    (optional, can be typed in GUI)
"""

import os
import subprocess
import sys

# Windowed exe: child processes (pdfinfo/pdftoppm/tesseract) each open a
# console window unless created with CREATE_NO_WINDOW. Patch Popen before
# pdf2image/pytesseract are imported so they pick up the quiet version.
if sys.platform == "win32":
    class _QuietPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _QuietPopen

from dotenv import load_dotenv

from gui import SendPDFApp


def main():
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(base_path, ".env"))

    required = ["APP_CLIENT_ID", "DIRECTORY_TENANT_ID", "VALUE"]
    env: dict[str, str] = {}
    missing: list[str] = []
    optional = ["SECRET_ID", "SENDER_EMAIL", "APP_CLIENT_ID-2", "VALUE-2", "SECRET_ID-2"]
    for key in required + optional:
        val = os.getenv(key, "")
        if val:
            env[key] = val
        elif key in required:
            missing.append(key)

    if missing:
        print(f"ERROR: Missing required .env variables: {', '.join(missing)}")
        print("\nExpected .env format:")
        for k in required:
            print(f"  {k}=...")
        sys.exit(1)

    app = SendPDFApp(env)
    app.mainloop()


if __name__ == "__main__":
    main()
