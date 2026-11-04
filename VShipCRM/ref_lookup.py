"""Look up a reference number and customer name from VShip CRM using a
booking number."""

from __future__ import annotations
import json
from difflib import SequenceMatcher
import requests
from VShipCRM.signInVShipCRM import get_valid_token, reauthenticate

SEARCH_URL = "https://vship2000-prod-api.azurewebsites.net/api/Bookings/searchBooking"


def _search_booking(booking_no: str, token: str) -> requests.Response:
    return requests.get(
        f"{SEARCH_URL}?searchText={booking_no}&page=1&pageSize=50",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=10,
        )


def _match_rank(candidate: str, booking_no: str) -> tuple[int, int, float]:
    """Rank how well *candidate* matches *booking_no* (lower sorts better)."""
    cand = (candidate or "").strip().upper()
    wanted = booking_no.strip().upper()

    if cand == wanted:
        tier = 0
    elif cand.startswith(wanted):
        tier = 1
    elif wanted in cand:
        tier = 2
    else:
        tier = 3

    # Prefer the least amount of extra text (e.g. "EBKG123" over
    # "EBKG123_Copy_76099"), then fall back to overall similarity.
    return tier, abs(len(cand) - len(wanted)), -SequenceMatcher(None, cand, wanted).ratio()


def _best_match(records: list[dict], booking_no: str) -> dict:
    """Return the record whose bookingNo most closely matches *booking_no*."""
    return min(records, key=lambda rec: _match_rank(rec.get("bookingNo", ""), booking_no))


def getRefNumber(booking_no: str | None) -> tuple[str | None, str | None, str | None]:
    """Query VShip CRM for the reference number tied to *booking_no*.

    Args:
        booking_no: The carrier booking number to search for.

    Returns:
        A (booking_id, ref_number, customer_name) tuple.  Either some or all may be None
        if the lookup fails or no results are found.
    """
    book_id = None
    ref_no = None
    cust_name = None

    if not booking_no:
        print("No booking number provided")
        return None, None, None

    token = get_valid_token()
    if token is None:
        print("Could not authenticate with VShip CRM; skipping lookup.")
        return None, None, None

    resp = _search_booking(booking_no, token)
    if resp.status_code in (401, 403):
        print(f"CRM rejected the token (status {resp.status_code}). Re-authenticating once.")
        token = reauthenticate()
        if token is None:
            print("Re-authentication failed; skipping lookup.")
            return None, None, None
        resp = _search_booking(booking_no, token)
    resp.raise_for_status()
    try:
        records = resp.json().get("value").get("data")
        if not records:
            raise IndexError("no bookings returned")
        data = _best_match(records, booking_no)
        ref_no = data.get("referenceNo")
        cust_name = data.get("customerName")
        book_id = data.get("bookingId")
    except (KeyError, IndexError, AttributeError, TypeError) as e:
        print(f"Error parsing response JSON: {e}")
        raise json.JSONDecodeError(f"Unexpected JSON structure: {resp.text}", resp.text, 0)


    return book_id, ref_no, cust_name


if __name__ == "__main__":
    print(getRefNumber("EBKG17093338"))
