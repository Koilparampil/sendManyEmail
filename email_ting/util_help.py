import os
from dotenv import load_dotenv
import base64
import json
import requests
import msal
from pathlib import Path
from typing import Optional

from .util_sharepoint_help import do_full_upload

import sys
exe_file = sys.executable
exe_parent = os.path.dirname(exe_file)
dotenv_path = os.path.join(exe_parent, ".env")
load_dotenv(dotenv_path=dotenv_path, override=True)
CLIENT_ID = os.getenv('APP_CLIENT_ID') if not os.getenv('APP_CLIENT_ID')==None else ""
CLIENT_ID = CLIENT_ID if CLIENT_ID is not None else ""

CLIENT_SECRET = os.getenv('VALUE') if not os.getenv('VALUE')==None else ""
CLIENT_SECRET = CLIENT_SECRET if CLIENT_SECRET is not None else ""

TENANT_ID = os.getenv('DIRECTORY_TENANT_ID') if not os.getenv('DIRECTORY_TENANT_ID')==None else ""
TENANT_ID = TENANT_ID if TENANT_ID is not None else ""

SENDER = os.getenv('SENDER') if not os.getenv('SENDER')==None else ""
SENDER1 = SENDER if SENDER is not None else ""

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES = ["https://graph.microsoft.com/.default"]
GRAPH = "https://graph.microsoft.com/v1.0"

_APP: msal.ConfidentialClientApplication | None = None

def _get_app() -> msal.ConfidentialClientApplication:
    global _APP
    if _APP is None:
        if not TENANT_ID:
            raise RuntimeError(
                "DIRECTORY_TENANT_ID is not set. "
                "Make sure a .env file exists next to the executable."
            )
        _APP = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=AUTHORITY,
            client_credential=CLIENT_SECRET,
        )
    return _APP

class UploadError(Exception):
    pass


import base64, json

# def decode_jwt_no_verify(token):
#     payload_b64 = token.split(".")[1]
#     payload_b64 += "=" * (-len(payload_b64) % 4)
#     return json.loads(base64.urlsafe_b64decode(payload_b64))

def get_app_token():
    token = _get_app().acquire_token_for_client(scopes=SCOPES)
    if token is not None:
        if "access_token" not in token:
            raise RuntimeError(json.dumps(token, indent=2))
    else:
        raise RuntimeError("Failed to acquire token.")
    # claims = decode_jwt_no_verify(token['access_token'])
    # print("appid:", claims.get("appid"))
    # print("tid:", claims.get("tid"))
    # print("roles:", claims.get("roles"))
    return {'access_token' : token['access_token']}

def create_draft_message(access_token: str, user_id: str, subject: str, to_recipients: list[str| None], 
                         cc_recipients: Optional[list[str]] = None, body_html: str = "") -> str:
    url = f"{GRAPH}/users/{user_id}/messages"
    payload = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to_recipients],
    }
    if cc_recipients:
        payload["ccRecipients"] = [{"emailAddress": {"address": a}} for a in cc_recipients]

    r = requests.post(url,
        headers={"Authorization": f"Bearer {access_token}","Content-Type": "application/json",},
        data=json.dumps(payload)
        )
    r.raise_for_status()
    return r.json()["id"]  # draft message id

def create_attachment_upload_session(access_token: str, user_id: str, message_id: str,
                                     filename: str, file_size_bytes: int) -> str:
    # Used for 3MB - 150MB attachments
    url = f"{GRAPH}/users/{user_id}/messages/{message_id}/attachments/createUploadSession"
    payload = {
        "AttachmentItem": {
            "attachmentType": "file",
            "name": filename,
            "size": file_size_bytes,
            "contentType": "application/pdf",
        }
    }
    r = requests.post(url, headers={"Authorization": f"Bearer {access_token}","Content-Type": "application/json",}, data=json.dumps(payload))
    r.raise_for_status()
    return r.json()["uploadUrl"]

def upload_in_chunks(upload_url: str, filepath: str, chunk_size: int = 4 * 1024 * 1024) -> dict:
    p = Path(filepath)
    total = p.stat().st_size
    start = 0
    with p.open("rb") as f:
        while start < total:
            end = min(start + chunk_size, total) - 1
            length = end - start + 1

            f.seek(start)
            chunk = f.read(length)

            headers = {
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{total}",
            }

            resp = requests.put(upload_url, headers=headers, data=chunk)

            # parse body once
            body = {}
            if resp.content:
                try:
                    body = resp.json()
                except Exception:
                    body = {}

            # Debugging stufff
            # if isinstance(body, dict) and body.get("nextExpectedRanges"):
            #     print(resp.status_code, headers["Content-Range"], "nextExpectedRanges:", body["nextExpectedRanges"])
            # else:
            #     print(resp.status_code, headers["Content-Range"], "no nextExpectedRanges")

            #If server expects more, continue (even if status is 200)
            if isinstance(body, dict) and body.get("nextExpectedRanges"):
                next_start = int(str(body["nextExpectedRanges"][0]).split("-")[0])
                start = next_start
                continue

            #Completed only when attachment object has id
            if resp.status_code in (200, 201) and isinstance(body, dict) and body.get("id"):
                return body
            elif resp.status_code in (200, 201) and isinstance(body, dict):
                return {"status": "completed", "http_status": resp.status_code}
            # Some servers respond 201 with body; if it's empty, that's unexpected here
            # raise RuntimeError(
            #     f"Unexpected response (not complete, no nextExpectedRanges): {resp.status_code}\n"
            #     f"Sent: {headers['Content-Range']}\n"
            #     f"Body: {resp.text}"
            # )

    raise RuntimeError("Upload loop ended without completion.")

def upload_in_compressed_chunks(upload_url: str, fileName, file_size, chunk_size: int = 4 * 1024 * 1024) -> dict:
    total = file_size
    start = 0
    with open(fileName) as f:
        while start < total:
            end = min(start + chunk_size, total) - 1
            length = end - start + 1

            f.seek(start)
            chunk = f.read(length)

            headers = {
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{total}",
            }

            resp = requests.put(upload_url, headers=headers, data=chunk)

            # parse body once
            body = {}
            if resp.content:
                try:
                    body = resp.json()
                except Exception:
                    body = {}

            # Debugging stufff
            # if isinstance(body, dict) and body.get("nextExpectedRanges"):
            #     print(resp.status_code, headers["Content-Range"], "nextExpectedRanges:", body["nextExpectedRanges"])
            # else:
            #     print(resp.status_code, headers["Content-Range"], "no nextExpectedRanges")

            #If server expects more, continue (even if status is 200)
            if isinstance(body, dict) and body.get("nextExpectedRanges"):
                next_start = int(str(body["nextExpectedRanges"][0]).split("-")[0])
                start = next_start
                continue

            #Completed only when attachment object has id
            if resp.status_code in (200, 201) and isinstance(body, dict) and body.get("id"):
                return body
            elif resp.status_code in (200, 201) and isinstance(body, dict):
                return {"status": "completed", "http_status": resp.status_code}
            # Some servers respond 201 with body; if it's empty, that's unexpected here
            # raise RuntimeError(
            #     f"Unexpected response (not complete, no nextExpectedRanges): {resp.status_code}\n"
            #     f"Sent: {headers['Content-Range']}\n"
            #     f"Body: {resp.text}"
            # )

    raise RuntimeError("Upload loop ended without completion.")

def send_draft(access_token: str, user_id: str, message_id: str) -> None:
    # POST /users/{id}/messages/{id}/send
    url = f"{GRAPH}/users/{user_id}/messages/{message_id}/send"
    r = requests.post(url, headers={"Authorization": f"Bearer {access_token}"})
    r.raise_for_status()

def send_small(listdictdict: list[dict[str, dict[str, str|None]]], subject: str, body: str,pdfPath: str):
    token = get_app_token()['access_token']
    if token is not None:
        pass
    else:
        raise RuntimeError("Failed to acquire token.")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    path = Path(pdfPath)
    pdf_name = path.name
    pdf_bytes = path.read_bytes()
    pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body
            },
            "toRecipients": listdictdict,
            "attachments": [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": pdf_name,
                "contentType": "application/pdf",
                "contentBytes": pdf_b64,
            }
        ],
        },
        "saveToSentItems": True
    }
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 202):
        raise RuntimeError(resp.text)
    
def send_medium(recipients_addy: list[str|None], subject: str, body: str, pdfFilePath:str):
    token = get_app_token()['access_token']
    draft_id = create_draft_message(
        token,
        SENDER1,
        subject=subject,
        body_html=body,
        to_recipients=recipients_addy,
    )
    # print("Draft created with ID:", draft_id)
    upload_url = create_attachment_upload_session(
        token,
        SENDER1,
        draft_id,
        filename=Path(pdfFilePath).name,
        file_size_bytes=Path(pdfFilePath).stat().st_size,
    )
    # print("Upload URL obtained:", upload_url)
    attachment_info = upload_in_chunks(upload_url, pdfFilePath)
    # print("Attachment info:", attachment_info)
    send_draft(token, SENDER1, draft_id)
    
def send_big(recipients_addy: list[dict[str, dict[str, str|None]]], subject: str, body: str, fileName: str):
    token = get_app_token()['access_token']
    if token is not None:
        pass
    else:
        raise RuntimeError("Failed to acquire token.")
    try:
        sharing_link = do_full_upload(token, fileName)
    except Exception as e:
        raise UploadError(f"Failed to upload file to SharePoint. {e}")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": f"{body}\nFile too large for email; it is uploaded to SharePoint:\nAccess the file here: {sharing_link}"
            },
            "toRecipients": recipients_addy,
        },
        "saveToSentItems": True
    }
    resp = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 202):
        raise RuntimeError(resp.text)