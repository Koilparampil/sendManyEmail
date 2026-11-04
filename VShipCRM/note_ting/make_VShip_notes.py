from pathlib import Path
from datetime import datetime, timedelta
import requests

from ..signInVShipCRM import sign_in_vshipcrm
from tinkyWinky_log_only import UserInputs, get_user_inputs


def on_response(resp):
    global pdf_url
    ct = resp.headers.get("content-type", "")
    if "application/json; charset=utf-8" in ct.lower() and resp.ok:
        pdf_url = resp.json()
        
def normalize(s) -> str:
    return (
        s.split(",")[0]
        .split("_")[0]
        .split(" ")[0]
        .strip()
    )

class NoteError(Exception):
    pass
    
def note_writing(chosen: dict, note: str | None = None, retries_left: int = 2):
    if (Path('auth_for_VshipCRM.txt').exists() ) and ( (datetime.now() - datetime.fromtimestamp(Path('auth_for_VshipCRM.txt').stat().st_mtime)) < timedelta(hours = 1) ):
        print("Using existing authentication state.")
    else:
        inputs: UserInputs = get_user_inputs()
        sign_in_vshipcrm(inputs.username, inputs.password)
    with open('auth_for_VshipCRM.txt', 'r') as f:
        token = f.read().strip()
    retry_after_reauth = False
    try:
        resp_note = requests.post(
        f"https://vship2000-prod-api.azurewebsites.net/api/Bookings/{chosen['booking_id']}/comments",
        json={
            "comment": note if note is not None else "VALS RECEIVED",
            "commentType": 1,
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            'sec-ch-ua': '"Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Referer": "https://vshipcrm.com/"},
        )
        if resp_note.status_code != 201 and resp_note.status_code not in [400,401,403]:
            raise ValueError(f"Note creation failed with status code: {resp_note.status_code}\n{resp_note.text}")
        elif resp_note.status_code in [401,403]:
            if retries_left <= 0:
                raise ValueError(
                    f"Note creation failed with status code: {resp_note.status_code} "
                    f"after re-authenticating. Giving up.\n{resp_note.text}"
                )
            print(f"Note creation failed with status code: {resp_note.status_code}. "
                  f"ReSigning In ({retries_left} retr{'y' if retries_left == 1 else 'ies'} left)")
            inputs: UserInputs = get_user_inputs()
            sign_in_vshipcrm(inputs.username, inputs.password)
            retry_after_reauth = True
        elif resp_note.status_code == 400:
            raise ValueError(f"Note creation failed with status code: {resp_note.status_code}\n{resp_note.text}")
    except NoteError:
        raise
    except Exception as e:
        raise NoteError(f"Error creating note: {e}")
    if retry_after_reauth:
        note_writing(chosen, note, retries_left - 1)

        
if __name__ == "__main__":
    print("Starting note creation process...")
    note_writing({"booking_id": "72892", "Titles": 1, "Salvage Certificates": 4, "Bills of Sale": 0, "Other Documents": 1})