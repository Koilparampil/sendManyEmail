import requests
from pathlib import Path
from datetime import datetime, timedelta

VSHIP_LOGIN_URL = "https://vship2000-prod-api.azurewebsites.net/api/Auth/login"
AUTH_FILE = 'auth_for_VshipCRM.txt'

# (connect, read) seconds. Without an explicit timeout requests blocks forever
# on a stalled connection, which freezes the whole application.
REQUEST_TIMEOUT = (10, 30)

def sign_in_vshipcrm(vship_user, vship_password):
    resplogin = requests.post(
        VSHIP_LOGIN_URL,
        json={
            "password": f"{vship_password}",
            "username": f"{vship_user}",
        },
        headers={"Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                    'sec-ch-ua': '"Chromium";v="143", "Not A(Brand";v="24"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
                    "Referer": "https://vshipcrm.com/"},
        timeout=REQUEST_TIMEOUT,
        )
    if resplogin.status_code != 200:
        print("Login failed with status code:", resplogin.status_code)
        return None
    token = resplogin.json().get("value").get("token")
    with open(AUTH_FILE, 'w') as f:
        f.write(token)
    print("Successfully logged in and saved token to 'auth_for_VshipCRM.txt'.")
    return token


def get_valid_token() -> str | None:
    """Return the cached token if the auth file is under an hour old,
    otherwise prompt for credentials and sign in once.

    Returns None if the sign-in failed or was cancelled.
    """
    p = Path(AUTH_FILE)
    if p.exists() and datetime.now() - datetime.fromtimestamp(p.stat().st_mtime) < timedelta(hours=1):
        print("Using existing authentication state.")
        return p.read_text().strip()
    return reauthenticate()


def reauthenticate() -> str | None:
    """Prompt for credentials and attempt to sign in exactly once.

    Returns the new token, or None if the login failed or the user
    cancelled the dialog. Never loops or retries on its own.
    """
    from tinkyWinky_log_only import get_user_inputs
    try:
        inputs = get_user_inputs()
    except SystemExit:
        print("Login cancelled.")
        return None
    return sign_in_vshipcrm(inputs.username, inputs.password)
