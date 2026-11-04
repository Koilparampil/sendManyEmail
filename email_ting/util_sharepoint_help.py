import os
import json
from pathlib import Path
from dotenv import load_dotenv
import msal
import requests

GRAPH = "https://graph.microsoft.com/v1.0"

import sys
exe_file = sys.executable
exe_parent = os.path.dirname(exe_file)
dotenv_path = os.path.join(exe_parent, ".env")
load_dotenv(dotenv_path=dotenv_path, override=True)
HOSTNAME = os.getenv('HOSTNAME') if not os.getenv('HOSTNAME')==None else ""
HOSTNAME = "" if HOSTNAME is None else HOSTNAME


SITE_PATH = os.getenv('SITE_PATH') if not os.getenv('SITE_PATH')==None else ""
SITE_PATH = "" if SITE_PATH is None else SITE_PATH

DRIVE_NAME = os.getenv('DRIVE_NAME') if not os.getenv('DRIVE_NAME')==None else ""
DRIVE_NAME = "" if DRIVE_NAME is None else DRIVE_NAME

DEST_FOLDER = os.getenv('DEST_FOLDER') if not os.getenv('DEST_FOLDER')==None else ""
DEST_FOLDER = "" if DEST_FOLDER is None else DEST_FOLDER


def get_site_id(token: str, hostname, site_path) -> str:
    """
    hostname: "vship.sharepoint.com"
    """
    hostname = HOSTNAME
    site_path = SITE_PATH
    url = f"{GRAPH}/sites/{hostname}:{site_path}"
    r = requests.get(url,headers={"Authorization": f"Bearer {token}","Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()["id"]

def get_drive_id(token: str, site_id: str, drive_name : str |None = DRIVE_NAME) -> str:
    """
    Lists document libraries and picks the one with matching name.
    """
    url = f"{GRAPH}/sites/{site_id}/drives"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}","Content-Type": "application/json"})
    r.raise_for_status()

    drives = r.json().get("value", [])
    for d in drives:
        if d.get("name") == drive_name:
            return d["id"]

    names = [d.get("name") for d in drives]
    raise RuntimeError(f"Drive '{drive_name}' not found. Available: {names}")

def create_upload_session(token: str, drive_id: str, dest_path_in_drive: str) -> str:
    """
    dest_path_in_drive example: "Shared Documents/Uploads/report.pdf"
    """
    url = f"{GRAPH}/drives/{drive_id}/root:/{dest_path_in_drive}:/createUploadSession"
    payload = {
        "item": {
            "@microsoft.graph.conflictBehavior": "replace"
        }
    }
    r = requests.post(url, headers={"Authorization": f"Bearer {token}","Content-Type": "application/json"}, data=json.dumps(payload))
    r.raise_for_status()
    return r.json()["uploadUrl"]

def upload_file_to_session(upload_url: str, fileName: str, chunk_size: int = 320 * 1024 * 20) -> dict:
    """
    chunk_size MUST be a multiple of 320 KiB. :contentReference[oaicite:2]{index=2}
    Returns the final DriveItem JSON on success.
    """

    if chunk_size % (320 * 1024) != 0:
        raise ValueError("chunk_size must be a multiple of 320 KiB (320*1024).")

    start = 0
    total = Path(fileName).stat().st_size
    with open(fileName, "rb") as f:
        while start < total:
            end = min(start + chunk_size, total) - 1
            length = end - start + 1

            f.seek(start)
            chunk = f.read(length)

            headers = {
                "Content-Length": str(length),
                "Content-Range": f"bytes {start}-{end}/{total}",
            }

            # uploadUrl is pre-authenticated; DO NOT add Authorization header
            resp = requests.put(upload_url, headers=headers, data=chunk)

            if resp.status_code in (200, 201):
                return resp.json()  # final DriveItem
            if resp.status_code == 202:
                # continue; Graph may provide nextExpectedRanges
                body = resp.json() if resp.content else {}
                ner = body.get("nextExpectedRanges")
                if ner:
                    start = int(str(ner[0]).split("-")[0])
                else:
                    start = end + 1
                continue

            raise RuntimeError(f"Upload failed: {resp.status_code} {resp.text}")

    raise RuntimeError("Upload ended unexpectedly without completion.")

def do_full_upload(token:str, fileName:str) -> str | None:
    
    site_id = get_site_id(token, HOSTNAME, SITE_PATH)
    drive_id = get_drive_id(token, site_id, DRIVE_NAME)
    filename = Path(fileName).name
    upload_url = create_upload_session(token, drive_id, f"{DEST_FOLDER}/{filename}")
    drive_item = upload_file_to_session(upload_url, fileName)
    url = f"{GRAPH}/drives/{drive_id}/items/{drive_item["id"]}/createLink"
    payload = {"type": "edit", "scope": "organization"}
    r = requests.post(url, headers={"Authorization": f"Bearer {token}","Content-Type": "application/json"}, data=json.dumps(payload))
    r.raise_for_status()
    return r.json()["link"]["webUrl"]
