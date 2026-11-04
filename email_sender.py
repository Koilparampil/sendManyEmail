import base64
import os
import time
from typing import Callable

import requests

_MAX_RETRIES = 3
_RETRY_BACKOFF = 10  # seconds between attempts


def send_email_with_pdf(
    access_token: str,
    sender: str,
    recipients: list[str],
    pdf_path: str,
    subject: str | None = None,
    body: str | None = None,
    backup_token: str | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    filename = os.path.basename(pdf_path)
    body = body or f"Please find attached: {filename}"

    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode()

    url = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": filename,
                    "contentType": "application/pdf",
                    "contentBytes": pdf_b64,
                }
            ],
        },
        "saveToSentItems": "true",
    }

    token = access_token
    last_exc: Exception = RuntimeError("No attempts made")
    attempt = 0
    attempts_left = _MAX_RETRIES
    while attempts_left > 0:
        attempt += 1
        attempts_left -= 1
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            return
        except requests.HTTPError as exc:
            # Rate-limited: switch to the second app registration once.
            # The switch doesn't consume a retry attempt.
            if exc.response.status_code == 429 and backup_token and token != backup_token:
                if log:
                    log("  429 rate-limited — retrying with second credentials")
                token = backup_token
                last_exc = exc
                attempts_left += 1
                continue
            raise  # other 4xx/5xx (or 429 with no backup left) — retrying won't help
        except Exception as exc:
            last_exc = exc
            if attempts_left > 0:
                time.sleep(_RETRY_BACKOFF * attempt)

    raise last_exc
