import os
import sys
from dotenv import load_dotenv
import base64
import json
import requests
import msal
from pathlib import Path

from .util_help import send_big, send_medium, send_small

class PDFSizeError(Exception):
    pass

exe_file = sys.executable
exe_parent = os.path.dirname(exe_file)
dotenv_path = os.path.join(exe_parent, ".env")
load_dotenv(dotenv_path=dotenv_path, override=True)

TITLESEND = os.getenv('TITLESEND') if not os.getenv('TITLESEND')==None else ""
TITLESENDLIST: list[str | None] = TITLESEND.split(',') if TITLESEND else [] # type: ignore


def send_email(subject: str, body: str, pdfFilepath: str, recipientsIG: list[str|None] = TITLESENDLIST):
    print(recipientsIG)
    file = Path(pdfFilepath)
    pdf_size_MB = file.stat().st_size / (1024 * 1024)
    formattedrecipients = [{"emailAddress": {"address": addy}} for addy in recipientsIG]
    match pdf_size_MB:
        case size if size <= 10:
            send_small(formattedrecipients, subject, body, pdfFilepath) #Works
        case size if 10 < size < 25:
            send_medium(recipientsIG, subject, body, pdfFilepath) #Works, Slow tho
        case size if 25 <= size:
            send_big(formattedrecipients, subject, body, pdfFilepath) #Also Works now, Still Slow tho.
        case _:
            raise PDFSizeError("Unexpected file size.")

    print("Email sent!")


if __name__ == "__main__":
    # try:
    #     send_email("Test Subject", "This is a test body.", "C:\\Users\\Chris\\Code-Documents\\VShipAutomation\\titleLabeling\\sample34.pdf")
    # except Exception as e:
    #     print(f"An error occurred while sending the email. {e}")
    print(TITLESENDLIST)