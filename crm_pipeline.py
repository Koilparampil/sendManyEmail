"""
Shared pipeline: CRM lookup -> save to SourceImages -> email -> CRM note.

Used by both the auto phase (OCR-extracted booking numbers) and the
manual review phase (hand-typed booking numbers).
"""

import os
import re
import shutil
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from email_sender import send_email_with_pdf
from VShipCRM.ref_lookup import getRefNumber
from VShipCRM.note_ting.make_VShip_notes import note_writing
from VShipCRM.signInVShipCRM import sign_in_vshipcrm
from tinkyWinky_log_only import SENDER, UserInputs, get_user_inputs

SOURCE_IMAGES_DIR = os.path.join(
    os.environ["USERPROFILE"], "VShip", "Elizabeth Lukic - 2026 SourceImages"
)


def _login_dialog(parent: tk.Misc) -> UserInputs:
    """Modal CRM login as a Toplevel of the running app.

    get_user_inputs() creates its own tk.Tk() + mainloop(), which never
    returns when another Tk root is already running — so inside the GUI we
    must use a Toplevel with wait_window() instead.
    Raises SystemExit if the user cancels, matching get_user_inputs().
    """
    dlg = tk.Toplevel(parent)
    dlg.title("Booking Tool Login")
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    result: list[UserInputs] = []
    username_var = tk.StringVar(master=dlg, value=SENDER)
    password_var = tk.StringVar(master=dlg)

    def submit():
        username = username_var.get().strip()
        password = password_var.get().strip()
        if not username or not password:
            return
        result.append(UserInputs(username=username, password=password))
        dlg.destroy()

    pad = {"padx": 10, "pady": 6}
    tk.Label(dlg, text="Username").grid(row=0, column=0, sticky="e", **pad)
    user_entry = tk.Entry(dlg, textvariable=username_var, width=42)
    user_entry.grid(row=0, column=1, sticky="we", **pad)
    tk.Label(dlg, text="Password").grid(row=1, column=0, sticky="e", **pad)
    pass_entry = tk.Entry(dlg, textvariable=password_var, show="*", width=42)
    pass_entry.grid(row=1, column=1, sticky="we", **pad)

    btn_frame = tk.Frame(dlg)
    btn_frame.grid(row=2, column=0, columnspan=2, pady=(8, 12))
    tk.Button(btn_frame, text="Cancel", width=12, command=dlg.destroy).pack(side="left", padx=6)
    tk.Button(btn_frame, text="Submit", width=12, command=submit).pack(side="left", padx=6)

    dlg.bind("<Return>", lambda _e: submit())
    dlg.bind("<Escape>", lambda _e: dlg.destroy())
    (pass_entry if SENDER else user_entry).focus_set()

    parent.wait_window(dlg)

    if not result:
        raise SystemExit("User cancelled.")
    return result[0]


def ensure_crm_auth(parent: tk.Misc | None = None) -> None:
    """Refresh the CRM token if missing/stale.

    MUST be called from the main thread — the login prompt opens a tkinter
    window, which is not safe from a background thread. Pass *parent* (the
    running app) so the prompt is a modal Toplevel; without a parent it
    falls back to get_user_inputs(), which only works when no other Tk
    root exists.
    """
    p = Path("auth_for_VshipCRM.txt")
    if p.exists() and datetime.now() - datetime.fromtimestamp(p.stat().st_mtime) < timedelta(hours=1):
        return
    if parent is not None:
        inputs = _login_dialog(parent)
    else:
        inputs = get_user_inputs()
    sign_in_vshipcrm(inputs.username, inputs.password)

    # sign_in_vshipcrm prints-and-returns on a failed login instead of
    # raising — verify the token file actually got refreshed.
    if not p.exists() or datetime.now() - datetime.fromtimestamp(p.stat().st_mtime) >= timedelta(minutes=1):
        raise RuntimeError("CRM login failed — check username/password.")


def val_path(ref_num: str, log: Callable[[str], None] = lambda _msg: None) -> str:
    """Return the '{ref_num}_VAL.pdf' path in SOURCE_IMAGES_DIR.

    Any existing '{ref_num}_VAL.pdf' or '{ref_num}_VAL{i}.pdf' copies are
    deleted first, so the new file always replaces them under the base name
    instead of being saved as '{ref_num}_VAL{i+1}.pdf'.
    """
    pattern = re.compile(rf"^{re.escape(ref_num)}_VAL\d*\.pdf$", re.IGNORECASE)
    if os.path.isdir(SOURCE_IMAGES_DIR):
        for name in sorted(os.listdir(SOURCE_IMAGES_DIR)):
            if pattern.match(name):
                os.remove(os.path.join(SOURCE_IMAGES_DIR, name))
                log(f"  Removed old copy {name}")
    return os.path.join(SOURCE_IMAGES_DIR, f"{ref_num}_VAL.pdf")


def process_pdf(
    token: str,
    sender: str,
    recipients: list[str],
    pdf_path: str,
    booking: str,
    log: Callable[[str], None],
    backup_token: str | None = None,
) -> str:
    """Run the full pipeline for one PDF. Returns the saved filename.

    Raises on any failure (lookup, save, send, or note) — caller decides
    whether to queue the PDF for manual entry.
    """
    book_id, ref_num, cust_name = getRefNumber(booking)
    if not book_id or not ref_num:
        raise LookupError(f"CRM lookup found no reference for booking {booking}")
    log(f"  CRM: ref {ref_num} — {cust_name}")

    os.makedirs(SOURCE_IMAGES_DIR, exist_ok=True)
    dest = val_path(ref_num, log)
    shutil.copy2(pdf_path, dest)
    filename = os.path.basename(dest)
    log(f"  Saved as {filename}")

    subject = f"Validated Titles {datetime.now().strftime('%m.%d.%Y')} - {booking}"
    send_email_with_pdf(
        token, sender, recipients, dest, subject=subject,
        backup_token=backup_token, log=log,
    )
    log(f"  ✓ Emailed {filename} to {len(recipients)} recipient(s)")

    note_writing(
        {"booking_id": book_id},
        note=f"VALS RECEIVED, SENT TO AAKASH, SAVED IN SOURCE IMAGES AS {filename}",
    )
    log("  ✓ CRM note added")

    return filename
