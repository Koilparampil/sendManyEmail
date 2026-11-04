import os
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

import requests

from auth import get_access_token
from crm_pipeline import ensure_crm_auth, process_pdf
from ocr_utils import (
    get_first_page_image,
    get_ocr_text,
    extract_booking_number,
    make_preview_photo,
    PREVIEW_AVAILABLE,
    OCR_AVAILABLE,
)

_PREVIEW_W = 720
_PREVIEW_H = 480

SENDER_EMAIL = os.getenv('SENDER_EMAIL') if not os.getenv('SENDER_EMAIL')==None else ""

class SendPDFApp(tk.Tk):
    def __init__(self, env: dict):
        super().__init__()
        self.env = env
        self.pdf_paths: list[str] = []
        self._preview_image = None  # keep reference so GC doesn't collect it
        self.title("Validate Titles PDF Email Sender")
        self.resizable(True, True)
        self.state("zoomed")  # start maximized
        self._build_ui()

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_ui(self):
        main = tk.Frame(self)
        main.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_preview_panel(main)
        self._build_controls_panel(main)

    def _build_preview_panel(self, parent):
        left = tk.LabelFrame(parent, text="PDF Preview (600 dpi)", width=_PREVIEW_W + 22)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        self.preview_canvas = tk.Canvas(
            left, width=_PREVIEW_W, bg="#dcdcdc"
        )
        self.preview_canvas.pack(fill="both", expand=True, padx=5, pady=(5, 2))
        self.preview_canvas.create_text(
            _PREVIEW_W // 2, _PREVIEW_H // 2,
            text="Select a PDF to preview",
            fill="gray", justify="center",
        )

        self._preview_status = tk.Label(left, text="", fg="gray", wraplength=_PREVIEW_W - 10)
        self._preview_status.pack(pady=(0, 4))

    def _build_controls_panel(self, parent):
        right = tk.Frame(parent)
        right.pack(side="left", fill="both", expand=True)

        pad = {"padx": 4, "pady": 3}

        # Sender
        tk.Label(right, text="Sender:", anchor="w").pack(fill="x", **pad)
        self.sender_var = tk.StringVar(value=SENDER_EMAIL)
        tk.Entry(right, textvariable=self.sender_var).pack(fill="x", **pad)

        # Recipients listbox
        tk.Label(right, text="Recipients:", anchor="w").pack(fill="x", **pad)

        rec_list_frame = tk.Frame(right)
        rec_list_frame.pack(fill="x", **pad)
        self.recipient_listbox = tk.Listbox(rec_list_frame, height=3, selectmode=tk.EXTENDED)
        self.recipient_listbox.pack(side="left", fill="both", expand=True)
        self.recipient_listbox.insert(tk.END, "aakash@vship2000.com")
        rec_scroll = tk.Scrollbar(rec_list_frame, orient="vertical", command=self.recipient_listbox.yview)
        rec_scroll.pack(side="right", fill="y")
        self.recipient_listbox.config(yscrollcommand=rec_scroll.set)

        rec_add_frame = tk.Frame(right)
        rec_add_frame.pack(fill="x", **pad)
        self.new_recipient_var = tk.StringVar()
        rec_entry = tk.Entry(rec_add_frame, textvariable=self.new_recipient_var)
        rec_entry.pack(side="left", fill="x", expand=True)
        rec_entry.bind("<Return>", lambda _e: self._add_recipient())
        tk.Button(rec_add_frame, text="Add", command=self._add_recipient).pack(side="left", padx=(4, 0))
        tk.Button(rec_add_frame, text="Remove Selected", command=self._remove_recipient).pack(side="left", padx=(4, 0))

        # Booking number
        bk_hdr = tk.Frame(right)
        bk_hdr.pack(fill="x", **pad)
        tk.Label(bk_hdr, text="Booking #:", anchor="w").pack(side="left")
        ocr_hint = "(OCR ready)" if OCR_AVAILABLE else "(install pytesseract + tesseract for OCR)"
        tk.Label(bk_hdr, text=ocr_hint, fg="gray", font=("TkDefaultFont", 8)).pack(side="left", padx=(6, 0))

        bk_row = tk.Frame(right)
        bk_row.pack(fill="x", **pad)
        self.booking_var = tk.StringVar()
        tk.Entry(bk_row, textvariable=self.booking_var).pack(side="left", fill="x", expand=True)
        tk.Button(bk_row, text="Re-extract OCR", command=self._reextract_ocr).pack(side="left", padx=(4, 0))

        # PDF list
        tk.Label(right, text="PDFs to send  (click a file to preview):", anchor="w").pack(fill="x", **pad)

        pdf_btn_row = tk.Frame(right)
        pdf_btn_row.pack(fill="x", **pad)
        tk.Button(pdf_btn_row, text="Add PDFs…", command=self._add_pdfs).pack(side="left")
        tk.Button(pdf_btn_row, text="Remove Selected", command=self._remove_selected).pack(side="left", padx=(8, 0))
        tk.Button(pdf_btn_row, text="Clear All", command=self._clear_pdfs).pack(side="left", padx=(8, 0))

        pdf_list_frame = tk.Frame(right)
        pdf_list_frame.pack(fill="x", **pad)
        self.listbox = tk.Listbox(pdf_list_frame, selectmode=tk.EXTENDED, height=5)
        self.listbox.pack(side="left", fill="both", expand=True)
        pdf_scroll = tk.Scrollbar(pdf_list_frame, orient="vertical", command=self.listbox.yview)
        pdf_scroll.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=pdf_scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_pdf_select)

        # Send button
        self.send_btn = tk.Button(right, text="Send Emails", command=self._on_send, height=2)
        self.send_btn.pack(fill="x", **pad)

        # Log
        self.log_box = scrolledtext.ScrolledText(right, height=7, state="disabled")
        self.log_box.pack(fill="both", expand=True, **pad)

    # -------------------------------------------------------------------------
    # Recipient management
    # -------------------------------------------------------------------------

    def _add_recipient(self):
        email = self.new_recipient_var.get().strip()
        if not email:
            return
        if email not in self.recipient_listbox.get(0, tk.END):
            self.recipient_listbox.insert(tk.END, email)
        self.new_recipient_var.set("")

    def _remove_recipient(self):
        for i in reversed(self.recipient_listbox.curselection()):
            self.recipient_listbox.delete(i)

    # -------------------------------------------------------------------------
    # PDF list management
    # -------------------------------------------------------------------------

    def _add_pdfs(self):
        files = filedialog.askopenfilenames(
            title="Select PDF files",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        first_new_idx = None
        for f in files:
            if f not in self.pdf_paths:
                self.pdf_paths.append(f)
                self.listbox.insert(tk.END, os.path.basename(f))
                if first_new_idx is None:
                    first_new_idx = len(self.pdf_paths) - 1

        if first_new_idx is not None:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(first_new_idx)
            self.listbox.see(first_new_idx)
            self._update_preview(self.pdf_paths[first_new_idx])

    def _remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            self.pdf_paths.pop(i)

    def _clear_pdfs(self):
        self.listbox.delete(0, tk.END)
        self.pdf_paths.clear()
        self._reset_preview()

    # -------------------------------------------------------------------------
    # PDF preview / OCR
    # -------------------------------------------------------------------------

    def _on_pdf_select(self, _event=None):
        sel = self.listbox.curselection()
        if sel:
            self._update_preview(self.pdf_paths[sel[-1]])

    def _canvas_center(self):
        self.update_idletasks()
        return (
            max(self.preview_canvas.winfo_width(), _PREVIEW_W) // 2,
            max(self.preview_canvas.winfo_height(), _PREVIEW_H) // 2,
        )

    def _update_preview(self, pdf_path: str):
        self._preview_status.config(text="Rendering…")
        self.preview_canvas.delete("all")
        cx, cy = self._canvas_center()
        self.preview_canvas.create_text(cx, cy, text="Loading…", fill="gray")
        max_w = max(self.preview_canvas.winfo_width(), _PREVIEW_W) - 10
        max_h = max(self.preview_canvas.winfo_height(), _PREVIEW_H) - 10
        threading.Thread(
            target=self._preview_thread, args=(pdf_path, max_w, max_h), daemon=True
        ).start()

    def _preview_thread(self, pdf_path: str, max_w: int, max_h: int):
        image = get_first_page_image(pdf_path)
        photo = make_preview_photo(image, max_width=max_w, max_height=max_h)
        booking = extract_booking_number(image)
        self.after(0, self._apply_preview, photo, booking)

    def _apply_preview(self, photo, booking: str):
        cx, cy = self._canvas_center()
        self.preview_canvas.delete("all")
        if photo:
            self._preview_image = photo
            self.preview_canvas.create_image(cx, cy, anchor="center", image=photo)
            self._preview_status.config(text="")
        else:
            hint = (
                "Preview unavailable\n(pip install pdf2image pillow\n+ install poppler)"
                if not PREVIEW_AVAILABLE
                else "Could not render this PDF"
            )
            self.preview_canvas.create_text(cx, cy, text=hint, fill="gray", justify="center")
            self._preview_status.config(text="")

        if booking:
            self.booking_var.set(booking)

    def _reextract_ocr(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("No selection", "Click a PDF in the list first.")
            return
        pdf_path = self.pdf_paths[sel[-1]]
        self._update_preview(pdf_path)
        threading.Thread(target=self._log_ocr_text, args=(pdf_path,), daemon=True).start()

    def _log_ocr_text(self, pdf_path: str):
        image = get_first_page_image(pdf_path)
        raw, err = get_ocr_text(image)
        self._log("--- Raw OCR text ---")
        if err:
            self._log(f"OCR ERROR: {err}")
        else:
            self._log(raw if raw.strip() else "(no text recognized)")
        self._log("--- End OCR text ---")

    def _reset_preview(self):
        cx, cy = self._canvas_center()
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(
            cx, cy, text="Select a PDF to preview", fill="gray", justify="center",
        )
        self._preview_status.config(text="")
        self._preview_image = None

    # -------------------------------------------------------------------------
    # Logging (called from background threads — safe via after())
    # -------------------------------------------------------------------------

    def _log(self, msg: str):
        self.after(0, self._log_direct, msg)

    def _log_direct(self, msg: str):
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    # -------------------------------------------------------------------------
    # Send logic
    # -------------------------------------------------------------------------

    def _on_send(self):
        sender = self.sender_var.get().strip()
        recipients = list(self.recipient_listbox.get(0, tk.END))

        if not sender:
            messagebox.showwarning("Missing info", "Sender email is empty.")
            return
        if not recipients:
            messagebox.showwarning("Missing info", "Add at least one recipient.")
            return
        if not self.pdf_paths:
            messagebox.showwarning("No PDFs", "Add at least one PDF file.")
            return

        # CRM login must happen on the main thread (it may open a login window)
        try:
            ensure_crm_auth(self)
        except SystemExit:
            messagebox.showwarning("CRM login", "CRM login cancelled — nothing sent.")
            return
        except Exception as e:
            messagebox.showerror("CRM login", f"CRM login failed: {e}")
            return

        self.send_btn.config(state="disabled", text="Sending…")
        threading.Thread(
            target=self._send_all,
            args=(sender, recipients, list(self.pdf_paths)),
            daemon=True,
        ).start()

    def _send_all(self, sender: str, recipients: list[str], pdfs: list[str]):
        self._log("Authenticating…")
        try:
            token = get_access_token(
                self.env["APP_CLIENT_ID"],
                self.env["DIRECTORY_TENANT_ID"],
                self.env["VALUE"],
            )
        except Exception as e:
            self._log(f"Authentication failed: {e}")
            self.after(0, lambda: self.send_btn.config(state="normal", text="Send Emails"))
            return

        # Second app registration (optional) — used automatically if a send
        # comes back 429 rate-limited
        backup_token = None
        if self.env.get("APP_CLIENT_ID-2") and self.env.get("VALUE-2"):
            try:
                backup_token = get_access_token(
                    self.env["APP_CLIENT_ID-2"],
                    self.env["DIRECTORY_TENANT_ID"],
                    self.env["VALUE-2"],
                )
                self._log("Backup credentials ready.")
            except Exception as e:
                self._log(f"Warning: backup credential auth failed: {e}")

        self._log("Authenticated.\n")
        self._log(f"Recipients: {', '.join(recipients)}\n")

        success = 0
        manual_queue: list[str] = []
        for i, pdf in enumerate(pdfs, 1):
            name = os.path.basename(pdf)
            self._log(f"[{i}/{len(pdfs)}] {name}")

            image = get_first_page_image(pdf)
            bk = extract_booking_number(image)
            if not bk:
                self._log("  ✗ No booking number found — queued for manual entry")
                manual_queue.append(pdf)
                continue

            self._log(f"  Booking # (OCR): {bk}")
            try:
                process_pdf(token, sender, recipients, pdf, bk, self._log, backup_token=backup_token)
                success += 1
            except requests.HTTPError as e:
                self._log(f"  ✗ HTTP {e.response.status_code}: {e.response.text[:200]} — queued for manual entry")
                manual_queue.append(pdf)
            except Exception as e:
                self._log(f"  ✗ {e} — queued for manual entry")
                manual_queue.append(pdf)

            if i < len(pdfs):
                time.sleep(1)

        self._log(f"\nAuto phase done — Sent: {success}, Needs manual entry: {len(manual_queue)}")
        if manual_queue:
            self._log("Queued for manual entry:")
            for pdf in manual_queue:
                self._log(f"  • {os.path.basename(pdf)}")
            self.after(0, self._start_manual_review, token, sender, recipients, manual_queue, backup_token)
        self.after(0, lambda: self.send_btn.config(state="normal", text="Send Emails"))

    def _start_manual_review(self, token: str, sender: str, recipients: list[str], pdfs: list[str], backup_token: str | None):
        ManualReviewWindow(self, token, sender, recipients, pdfs, backup_token)


class ManualReviewWindow(tk.Toplevel):
    """Shown after the auto phase for PDFs whose booking number couldn't be
    extracted (or whose CRM lookup failed). The filename is a clickable link
    that opens the PDF in the default browser; booking number is typed below.
    Each submission runs the same lookup -> save -> email -> note pipeline
    as the auto phase."""

    def __init__(self, app: SendPDFApp, token: str, sender: str, recipients: list[str], pdfs: list[str], backup_token: str | None = None):
        super().__init__(app)
        self.app = app
        self.token = token
        self.backup_token = backup_token
        self.sender = sender
        self.recipients = recipients
        self.queue: list[str] = list(pdfs)
        self.total = len(pdfs)
        self.title("Manual Booking Entry")
        self.geometry("460x260")
        self.resizable(False, False)
        self._build_ui()
        self._show_current()

    # -- UI --------------------------------------------------------------

    def _build_ui(self):
        main = tk.Frame(self)
        main.pack(fill="both", expand=True, padx=12, pady=8)

        pad = {"padx": 4, "pady": 3}
        self.progress_lbl = tk.Label(main, text="", font=("TkDefaultFont", 10, "bold"))
        self.progress_lbl.pack(fill="x", **pad)
        self.file_lbl = tk.Label(
            main, text="", wraplength=420, justify="left",
            fg="blue", cursor="hand2", font=("TkDefaultFont", 9, "underline"),
        )
        self.file_lbl.pack(fill="x", **pad)
        self.file_lbl.bind("<Button-1>", lambda _e: self._open_pdf())

        tk.Label(main, text="Booking #:", anchor="w").pack(fill="x", **pad)
        self.booking_var = tk.StringVar()
        self.booking_entry = tk.Entry(main, textvariable=self.booking_var)
        self.booking_entry.pack(fill="x", **pad)
        self.booking_entry.bind("<Return>", lambda _e: self._submit())

        self.submit_btn = tk.Button(main, text="Look Up && Send", command=self._submit, height=2)
        self.submit_btn.pack(fill="x", **pad)
        self.skip_btn = tk.Button(main, text="Skip This PDF", command=self._skip)
        self.skip_btn.pack(fill="x", **pad)

        self.status_lbl = tk.Label(main, text="", wraplength=420, justify="left", fg="gray")
        self.status_lbl.pack(fill="x", **pad)

    def _show_current(self):
        if not self.queue:
            self.app._log("\nManual entry complete.")
            self.destroy()
            return

        pdf = self.queue[0]
        done = self.total - len(self.queue)
        self.progress_lbl.config(text=f"PDF {done + 1} of {self.total}")
        self.file_lbl.config(text=os.path.basename(pdf))
        self.booking_var.set("")
        self.status_lbl.config(text="Click the filename to open the PDF, then enter its booking number.")
        self.booking_entry.focus_set()

    def _open_pdf(self):
        if not self.queue:
            return
        webbrowser.open(Path(self.queue[0]).as_uri())

    # -- Actions ----------------------------------------------------------

    def _submit(self):
        if not self.queue:
            return
        booking = self.booking_var.get().strip().upper().replace(" ", "")
        if not booking:
            messagebox.showwarning("Missing info", "Enter a booking number.", parent=self)
            return

        # Refresh CRM token if stale — main thread, may open the login window
        try:
            ensure_crm_auth(self)
        except SystemExit:
            self.status_lbl.config(text="CRM login cancelled.")
            return
        except Exception as e:
            self.status_lbl.config(text=f"CRM login failed: {e}")
            return

        pdf = self.queue[0]
        self.submit_btn.config(state="disabled")
        self.skip_btn.config(state="disabled")
        self.status_lbl.config(text="Processing…")
        threading.Thread(
            target=self._submit_thread, args=(pdf, booking), daemon=True
        ).start()

    def _submit_thread(self, pdf: str, booking: str):
        name = os.path.basename(pdf)
        self.app._log(f"[manual] {name} — booking {booking}")
        try:
            process_pdf(self.token, self.sender, self.recipients, pdf, booking, self.app._log, backup_token=self.backup_token)
        except Exception as e:
            self.app._log(f"  ✗ {e}")
            self.after(0, self._submit_failed, str(e))
            return
        self.after(0, self._submit_succeeded)

    def _submit_failed(self, err: str):
        self.status_lbl.config(text=f"Failed: {err}\nFix the booking number and retry, or skip.")
        self.submit_btn.config(state="normal")
        self.skip_btn.config(state="normal")

    def _submit_succeeded(self):
        self.queue.pop(0)
        self.submit_btn.config(state="normal")
        self.skip_btn.config(state="normal")
        self._show_current()

    def _skip(self):
        if not self.queue:
            return
        skipped = self.queue.pop(0)
        self.app._log(f"[manual] Skipped: {os.path.basename(skipped)}")
        self._show_current()
