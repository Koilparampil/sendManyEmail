"""Send a PDF as an email attachment with Microsoft Graph (sendMail)."""

from __future__ import annotations

import base64
import email.utils
import logging
import math
import os
import ssl
import time
from collections.abc import Callable
from datetime import datetime, timezone

import requests
import urllib3

from graph_auth import GraphAuthError, GraphTokenProvider

log = logging.getLogger(__name__)

SENDMAIL_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
SEND_TIMEOUT = (10, 120)  # (connect, read) seconds

CONNECT_ATTEMPTS = 3  # tries in total when no connection can be made at all
CONNECT_BACKOFF = 10  # seconds: wait 10 s, then 20 s
THROTTLE_RETRIES = 5  # retries after HTTP 429 or 503
DEFAULT_RETRY_AFTER = 30  # seconds, when Microsoft doesn't say how long to wait
MAX_RETRY_AFTER = 300  # asked to wait longer than this? Fail the send instead


class EmailError(Exception):
    """Sending failed. The message is shown to the user."""


class EmailMaybeSent(EmailError):
    """The request may have reached Microsoft, so the email may have gone out.

    It must not be resent without someone checking Sent Items first.
    """


class GraphMailer:
    """Sends mail through Graph, falling back to a second app registration
    when the first one is throttled."""

    def __init__(
        self,
        primary: GraphTokenProvider,
        backup: GraphTokenProvider | None = None,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.primary = primary
        self.backup = backup
        self._backup_works = backup is not None
        self._http = session or requests.Session()
        self._sleep = sleep

    def check_credentials(self) -> None:
        """Get a token for each registration now, so a bad secret shows up
        before any PDF is processed. Raises GraphAuthError for the primary."""
        self.primary.token()
        if self.backup is None:
            return
        try:
            self.backup.token()
            self._backup_works = True
            log.info("Backup credentials ready.")
        except GraphAuthError as exc:
            self._backup_works = False
            log.warning("The backup credentials don't work, so they won't be used: %s", exc)

    def send_pdf(
        self,
        sender: str,
        recipients: list[str],
        pdf_path: str,
        subject: str,
        body: str | None = None,
    ) -> None:
        """Email pdf_path to recipients from the sender mailbox.

        Raises EmailMaybeSent if the outcome is unknown, EmailError or
        GraphAuthError otherwise.
        """
        filename = os.path.basename(pdf_path)
        try:
            with open(pdf_path, "rb") as f:
                content = base64.b64encode(f.read()).decode("ascii")
        except OSError as exc:
            raise EmailError(f"Couldn't read {filename}: {exc}") from exc

        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body or f"Please find attached: {filename}"},
                "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
                "attachments": [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": filename,
                        "contentType": "application/pdf",
                        "contentBytes": content,
                    }
                ],
            },
            "saveToSentItems": True,
        }
        url = SENDMAIL_URL.format(sender=sender)

        provider = self.primary
        connect_failures = 0
        throttled = 0
        while True:
            token = provider.token()
            try:
                resp = self._http.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                    timeout=SEND_TIMEOUT,
                )
            except requests.RequestException as exc:
                if not request_never_sent(exc):
                    raise EmailMaybeSent(
                        f"The connection to Microsoft broke after the email was handed over "
                        f"({type(exc).__name__}), so it may already have been sent. "
                        "Check Sent Items before sending it again."
                    ) from exc
                connect_failures += 1
                if connect_failures >= CONNECT_ATTEMPTS:
                    raise EmailError(f"Couldn't connect to Microsoft Graph: {exc}") from exc
                wait = CONNECT_BACKOFF * connect_failures
                log.warning("  Couldn't connect to Microsoft Graph. Trying again in %d s…", wait)
                self._sleep(wait)
                continue

            if resp.status_code in (200, 202):
                return
            if resp.status_code == 429 and provider is self.primary and self.backup and self._backup_works:
                log.warning("  Throttled (HTTP 429). Retrying with the backup credentials…")
                provider = self.backup
                continue
            if resp.status_code in (429, 503):
                wait = retry_after_seconds(resp)
                if throttled >= THROTTLE_RETRIES:
                    raise EmailError(
                        f"Microsoft Graph is still busy (HTTP {resp.status_code}) after "
                        f"{throttled} retries. Try again later."
                    )
                if wait > MAX_RETRY_AFTER:
                    raise EmailError(
                        f"Microsoft Graph is busy (HTTP {resp.status_code}) and asked to wait {wait} s. "
                        "Try again later."
                    )
                throttled += 1
                log.warning(
                    "  Microsoft Graph is busy (HTTP %d). Waiting %d s, as it asked…", resp.status_code, wait
                )
                self._sleep(wait)
                continue
            raise EmailError(describe_graph_error(resp))


def request_never_sent(exc: requests.RequestException) -> bool:
    """True if exc means the request can't have reached the server, so
    sending it again can't produce a duplicate."""
    if isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.ProxyError)):
        return True
    reason = getattr(exc.args[0], "reason", None) if exc.args else None
    if isinstance(exc, requests.exceptions.SSLError):
        # Only a failed certificate check is certain to happen before sending.
        inner = reason.args[0] if reason is not None and reason.args else None
        return isinstance(inner, ssl.SSLCertVerificationError)
    if isinstance(exc, requests.exceptions.ConnectionError):
        # Connection refused, DNS failure, no network: nothing was sent.
        return isinstance(reason, urllib3.exceptions.NewConnectionError)
    return False  # read timeouts, dropped connections: the request may have arrived


def retry_after_seconds(resp: requests.Response, default: int = DEFAULT_RETRY_AFTER) -> int:
    """Seconds to wait according to the Retry-After header (at least 1)."""
    value = (resp.headers.get("Retry-After") or "").strip()
    if value.isdigit():
        return max(1, int(value))
    if value:
        try:
            when = email.utils.parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(1, math.ceil((when - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError):
            pass
    return default


def describe_graph_error(resp: requests.Response) -> str:
    detail = ""
    try:
        error = resp.json().get("error") or {}
        detail = ": ".join(part for part in (error.get("code"), error.get("message")) if part)
    except (ValueError, AttributeError):
        pass
    if not detail:
        detail = " ".join((resp.text or "").split())[:200]
    return f"Microsoft Graph rejected the email (HTTP {resp.status_code})" + (f": {detail}" if detail else ".")
