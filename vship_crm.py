"""VShip CRM API client: sign in, find a booking, and add a comment to it.

The client never asks for credentials itself. Without a usable token it
raises CrmAuthRequired; the caller then asks the user (the GUI does that on
the Tk main thread, the only thread allowed to open windows) and calls
sign_in().
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API_BASE = "https://vship2000-prod-api.azurewebsites.net/api"

# The token is cached in this file in the current working directory and
# reused for up to TOKEN_MAX_AGE.
TOKEN_FILE = Path("auth_for_VshipCRM.txt")
TOKEN_MAX_AGE = timedelta(hours=1)

# Seconds. A pair is (connect, read); a single number covers both.
LOGIN_TIMEOUT = (10, 30)
SEARCH_TIMEOUT = 10
COMMENT_TIMEOUT = 25

SEARCH_PAGE_SIZE = 50

# Sent with login and comment requests, matching the CRM's web front end.
_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "sec-ch-ua": '"Chromium";v="143", "Not A(Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
    ),
    "Referer": "https://vshipcrm.com/",
}


class CrmError(Exception):
    """A CRM request failed. The message is shown to the user."""


class CrmAuthRequired(CrmError):
    """There's no usable token, or the CRM rejected it."""


class CrmLoginFailed(CrmError):
    """Signing in didn't work."""


class BookingNotFound(CrmError):
    """No single CRM booking matches the booking number."""


@dataclass(frozen=True)
class CrmCredentials:
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class BookingMatch:
    booking_id: int | str
    booking_no: str  # as the CRM has it
    reference_no: str
    customer_name: str
    exact: bool  # False when the CRM booking number only contains the one searched for


# --- Choosing the right search result ------------------------------------------------

_COPY_RE = re.compile(r"[_\s-]COPY", re.IGNORECASE)


def normalize_booking_no(value) -> str:
    """Upper-case the value and drop everything except letters and digits."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def is_copy_variant(booking_no) -> bool:
    """True for CRM copies of a booking, such as "EBKG123_Copy_76099"."""
    return bool(_COPY_RE.search(str(booking_no or "")))


def contains_as_number(candidate: str, wanted: str) -> bool:
    """True if wanted appears in candidate with no digit right before or after it.

    "17093338" is in "EBKG17093338", but not in "170933381" or "917093338":
    those are different numbers.
    """
    start = candidate.find(wanted)
    while start != -1:
        end = start + len(wanted)
        digit_before = start > 0 and candidate[start - 1].isdigit()
        digit_after = end < len(candidate) and candidate[end].isdigit()
        if not digit_before and not digit_after:
            return True
        start = candidate.find(wanted, start + 1)
    return False


def pick_booking(records: list, booking_no: str) -> BookingMatch:
    """Choose the CRM search result that is booking_no. Raises BookingNotFound.

    - Copies (a booking number containing "_Copy") are never used.
    - An exact match wins, ignoring case, spaces and punctuation.
    - Otherwise, a booking whose number contains booking_no as a whole number
      is used: "17093338" finds "EBKG17093338".
    - A near miss such as EBKG17093339 for EBKG17093338 is never used.
    - If more than one booking qualifies, none is picked; a person decides.
    """
    wanted = normalize_booking_no(booking_no)
    if not wanted:
        raise BookingNotFound("There's no booking number to look up.")

    candidates: dict[object, dict] = {}
    for record in records:
        if not isinstance(record, dict) or is_copy_variant(record.get("bookingNo")):
            continue
        key = record.get("bookingId")
        candidates.setdefault(key if key is not None else id(record), record)

    exact, containing = [], []
    for record in candidates.values():
        number = normalize_booking_no(record.get("bookingNo"))
        if number == wanted:
            exact.append(record)
        elif contains_as_number(number, wanted):
            containing.append(record)

    for group, is_exact in ((exact, True), (containing, False)):
        if len(group) == 1:
            return _to_match(group[0], is_exact)
        if len(group) > 1:
            numbers = ", ".join(sorted(str(r.get("bookingNo")) for r in group))
            raise BookingNotFound(
                f"{len(group)} CRM bookings match {booking_no} ({numbers}), so none was picked."
            )

    returned = sorted(
        {str(r.get("bookingNo")) for r in records if isinstance(r, dict) and r.get("bookingNo")}
    )
    detail = ""
    if returned:
        shown = ", ".join(returned[:5]) + (", …" if len(returned) > 5 else "")
        detail = f" The search returned: {shown}."
    raise BookingNotFound(f"No CRM booking matches {booking_no}.{detail}")


def _to_match(record: dict, exact: bool) -> BookingMatch:
    booking_id = record.get("bookingId")
    reference_no = str(record.get("referenceNo") or "").strip()
    if booking_id in (None, "") or not reference_no:
        raise BookingNotFound(
            f"CRM booking {record.get('bookingNo')} has no booking ID or reference number."
        )
    return BookingMatch(
        booking_id=booking_id,
        booking_no=str(record.get("bookingNo") or ""),
        reference_no=reference_no,
        customer_name=str(record.get("customerName") or "").strip(),
        exact=exact,
    )


# --- The client --------------------------------------------------------------------------


class CrmClient:
    """Talks to the VShip CRM API. Never prompts; see the module docstring."""

    def __init__(self, token_file: Path = TOKEN_FILE, session: requests.Session | None = None):
        self._token_file = Path(token_file)
        self._http = session or requests.Session()
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_time: datetime | None = None
        self._load_saved_token()

    # Token handling

    def has_fresh_token(self) -> bool:
        with self._lock:
            return self._token_is_fresh()

    def forget_token(self) -> None:
        with self._lock:
            self._token = None
            self._token_time = None

    def sign_in(self, username: str, password: str) -> None:
        """Log in and save the token. Raises CrmLoginFailed."""
        try:
            resp = self._http.post(
                f"{API_BASE}/Auth/login",
                json={"password": password, "username": username},
                headers=_BROWSER_HEADERS,
                timeout=LOGIN_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise CrmLoginFailed(f"Couldn't reach VShip CRM to log in: {exc}") from exc
        if resp.status_code != 200:
            log.debug("CRM login failed: HTTP %s %s", resp.status_code, resp.text[:500])
            raise CrmLoginFailed(
                f"VShip CRM login failed (HTTP {resp.status_code}). Check your username and password."
            )
        try:
            token = resp.json()["value"]["token"]
        except (ValueError, KeyError, TypeError):
            token = None
        if not isinstance(token, str) or not token.strip():
            raise CrmLoginFailed("VShip CRM accepted the login but didn't send a token back.")
        token = token.strip()

        try:
            self._token_file.write_text(token)
        except OSError as exc:
            log.warning("Couldn't save the CRM token to %s: %s", self._token_file, exc)
        with self._lock:
            self._token = token
            self._token_time = datetime.now()
        log.info("Signed in to VShip CRM as %s.", username)

    # API calls

    def find_booking(self, booking_no: str) -> BookingMatch:
        """Search the CRM for booking_no and pick the matching booking.

        Raises BookingNotFound, CrmAuthRequired or CrmError.
        """
        headers = {**self._auth_header(), "Accept": "application/json"}
        try:
            resp = self._http.get(
                f"{API_BASE}/Bookings/searchBooking",
                params={"searchText": booking_no, "page": 1, "pageSize": SEARCH_PAGE_SIZE},
                headers=headers,
                timeout=SEARCH_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise CrmError(f"Couldn't reach VShip CRM to look up {booking_no}: {exc}") from exc
        self._check_status(resp, f"look up {booking_no}")

        try:
            value = resp.json().get("value")
        except (ValueError, AttributeError):
            value = None
        if not isinstance(value, dict):
            log.debug("Unexpected CRM search response: %s", resp.text[:1000])
            raise CrmError("VShip CRM sent an unexpected response to the booking search.")
        records = value.get("data") or []
        if not isinstance(records, list):
            log.debug("Unexpected CRM search response: %s", resp.text[:1000])
            raise CrmError("VShip CRM sent an unexpected response to the booking search.")

        log.debug(
            "CRM search for %s returned: %s",
            booking_no,
            [r.get("bookingNo") for r in records if isinstance(r, dict)],
        )
        return pick_booking(records, booking_no)

    def add_comment(self, booking_id: int | str, comment: str) -> None:
        """Add a comment (note) to a booking. Raises CrmAuthRequired or CrmError."""
        headers = {**_BROWSER_HEADERS, **self._auth_header()}
        try:
            resp = self._http.post(
                f"{API_BASE}/Bookings/{booking_id}/comments",
                json={"comment": comment, "commentType": 1},
                headers=headers,
                timeout=COMMENT_TIMEOUT,
            )
        except requests.exceptions.ReadTimeout as exc:
            raise CrmError(
                f"VShip CRM didn't answer within {COMMENT_TIMEOUT} seconds, so the note may or may not "
                "have been added. Check the booking before retrying."
            ) from exc
        except requests.RequestException as exc:
            raise CrmError(f"Couldn't reach VShip CRM to add the note: {exc}") from exc
        self._check_status(resp, "add the note")

    # Helpers

    def _load_saved_token(self) -> None:
        try:
            saved_at = datetime.fromtimestamp(self._token_file.stat().st_mtime)
            if datetime.now() - saved_at >= TOKEN_MAX_AGE:
                return
            token = self._token_file.read_text().strip()
        except OSError:
            return
        if token:
            self._token, self._token_time = token, saved_at

    def _token_is_fresh(self) -> bool:
        return (
            bool(self._token)
            and self._token_time is not None
            and datetime.now() - self._token_time < TOKEN_MAX_AGE
        )

    def _auth_header(self) -> dict[str, str]:
        with self._lock:
            token = self._token if self._token_is_fresh() else None
        if not token:
            raise CrmAuthRequired("Not logged in to VShip CRM.")
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _check_status(resp: requests.Response, action: str) -> None:
        if resp.status_code in (401, 403):
            raise CrmAuthRequired(f"VShip CRM rejected the login (HTTP {resp.status_code}).")
        if not resp.ok:
            body = " ".join(resp.text.split())[:200]
            log.debug("CRM couldn't %s: HTTP %s %s", action, resp.status_code, resp.text[:1000])
            raise CrmError(
                f"VShip CRM couldn't {action} (HTTP {resp.status_code})" + (f": {body}" if body else ".")
            )
