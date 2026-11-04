"""Tkinter user interface: the main window, the Manual Booking Entry window
and the CRM login dialog.

Only the Tk main thread touches widgets. Worker threads hand results back
through UiDispatcher.post(). When a worker needs CRM credentials, it asks the
main thread to show the login dialog and waits for the answer.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

from config import AppRegistration, Settings
from crm_pipeline import AskCredentials, CrmLoginCancelled, PdfJob, Pipeline
from email_sender import GraphMailer
from graph_auth import GraphAuthError, GraphTokenProvider
from ocr_utils import RENDER_DPI, OcrTools, PageAnalyzer, check_ocr_tools, fit_image
from vship_crm import CrmClient, CrmCredentials

try:
    from PIL import ImageTk
except ImportError:  # Pillow missing, or built without Tk support
    ImageTk = None

log = logging.getLogger(__name__)

APP_TITLE = "Validate Titles PDF Email Sender"
DEFAULT_RECIPIENTS = ("aakash@vship2000.com",)

_PREVIEW_W = 720
_PREVIEW_H = 480


class UiDispatcher:
    """Runs callbacks on the Tk main thread.

    Worker threads mustn't touch Tk, so they post() callbacks here and the
    main thread runs them from a short after() loop.
    """

    POLL_MS = 50
    MAX_PER_POLL = 200

    def __init__(self, root: tk.Misc):
        self._root = root
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._root.after(self.POLL_MS, self._drain)

    def post(self, callback: Callable, *args) -> None:
        self._queue.put((callback, args))

    def _drain(self) -> None:
        # Schedule the next poll first, so posted callbacks keep running while
        # a modal dialog opened by one of them is waiting for the user.
        try:
            self._root.after(self.POLL_MS, self._drain)
        except tk.TclError:
            return  # the window has been destroyed
        for _ in range(self.MAX_PER_POLL):
            try:
                callback, args = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                callback(*args)
            except Exception:
                log.exception("A screen update failed")


class UiLogHandler(logging.Handler):
    """Shows INFO and above in the main window's log box. Only the message
    text is shown; tracebacks go to the log file."""

    def __init__(self, dispatcher: UiDispatcher, write: Callable[[str], None]):
        super().__init__(level=logging.INFO)
        self._dispatcher = dispatcher
        self._write = write

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            self.handleError(record)
            return
        self._dispatcher.post(self._write, message)


class LoginDialog(tk.Toplevel):
    """Modal VShip CRM login. Use LoginDialog.ask() on the main thread."""

    @classmethod
    def ask(cls, parent: tk.Misc, username: str = "", message: str | None = None) -> CrmCredentials | None:
        dialog = cls(parent, username, message)
        parent.wait_window(dialog)
        return dialog.result

    def __init__(self, parent: tk.Misc, username: str, message: str | None):
        super().__init__(parent)
        self.result: CrmCredentials | None = None
        self.title("Booking Tool Login")
        self.resizable(False, False)
        self.transient(parent)

        pad = {"padx": 10, "pady": 6}
        row = 0
        if message:
            tk.Label(self, text=message, fg="firebrick", wraplength=380, justify="left").grid(
                row=row, column=0, columnspan=2, sticky="w", **pad
            )
            row += 1
        self._username = tk.StringVar(master=self, value=username)
        self._password = tk.StringVar(master=self)
        tk.Label(self, text="Username").grid(row=row, column=0, sticky="e", **pad)
        user_entry = tk.Entry(self, textvariable=self._username, width=42)
        user_entry.grid(row=row, column=1, sticky="we", **pad)
        tk.Label(self, text="Password").grid(row=row + 1, column=0, sticky="e", **pad)
        pass_entry = tk.Entry(self, textvariable=self._password, show="*", width=42)
        pass_entry.grid(row=row + 1, column=1, sticky="we", **pad)
        self._hint = tk.Label(self, text="", fg="firebrick")
        self._hint.grid(row=row + 2, column=0, columnspan=2)

        buttons = tk.Frame(self)
        buttons.grid(row=row + 3, column=0, columnspan=2, pady=(0, 12))
        tk.Button(buttons, text="Cancel", width=12, command=self.destroy).pack(side="left", padx=6)
        tk.Button(buttons, text="Submit", width=12, command=self._submit).pack(side="left", padx=6)

        self.bind("<Return>", lambda _e: self._submit())
        self.bind("<Escape>", lambda _e: self.destroy())
        (pass_entry if username else user_entry).focus_set()
        self._grab()

    def _grab(self, attempts: int = 20) -> None:
        try:
            self.grab_set()
        except tk.TclError:  # not viewable yet
            if attempts > 0:
                self.after(50, self._grab, attempts - 1)

    def _submit(self) -> None:
        username = self._username.get().strip()
        password = self._password.get().strip()
        if not username or not password:
            self._hint.config(text="Enter your username and password.")
            return
        self.result = CrmCredentials(username=username, password=password)
        self.destroy()


def _make_mailer(settings: Settings) -> GraphMailer:
    def provider(registration: AppRegistration) -> GraphTokenProvider:
        return GraphTokenProvider(settings.tenant_id, registration.client_id, registration.client_secret)

    backup = provider(settings.graph_backup) if settings.graph_backup else None
    return GraphMailer(provider(settings.graph), backup)


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class SendPDFApp(tk.Tk):
    def __init__(
        self,
        settings: Settings,
        log_path: str | None = None,
        *,
        crm: CrmClient | None = None,
        mailer: GraphMailer | None = None,
        analyzer: PageAnalyzer | None = None,
        check_tools: Callable[[], OcrTools] = check_ocr_tools,
    ):
        super().__init__()
        self.settings = settings
        self.ui = UiDispatcher(self)
        self.analyzer = analyzer or PageAnalyzer()
        self.mailer = mailer or _make_mailer(settings)
        self.pipeline = Pipeline(
            crm=crm or CrmClient(),
            mailer=self.mailer,
            analyzer=self.analyzer,
            source_images_dir=settings.source_images_dir,
        )
        self.jobs: list[PdfJob] = []  # the PDF list, in display order
        self._batch_running = False
        self._review: ManualReviewWindow | None = None
        self._crm_username = settings.crm_username
        self._preview_seq = 0  # bumped on every preview request; stale results are dropped
        self._previewed_job: PdfJob | None = None
        self._preview_photo = None  # keep a reference so Tk doesn't drop the image
        self._preview_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")

        self.title(APP_TITLE)
        self.resizable(True, True)
        try:
            self.state("zoomed")  # start maximised
        except tk.TclError:
            pass  # not supported outside Windows and macOS
        self._build_ui()

        self._log_handler = UiLogHandler(self.ui, self._append_log)
        logging.getLogger().addHandler(self._log_handler)
        self._log_startup(log_path)
        threading.Thread(target=self._check_ocr_tools, args=(check_tools,), name="ocr-check", daemon=True).start()

    def report_callback_exception(self, exc, val, tb):
        log.error("Unexpected error: %s", val, exc_info=(exc, val, tb))

    def destroy(self):
        handler = getattr(self, "_log_handler", None)
        if handler is not None:
            logging.getLogger().removeHandler(handler)
        pool = getattr(self, "_preview_pool", None)
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
        super().destroy()

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_ui(self):
        main = tk.Frame(self)
        main.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_preview_panel(main)
        self._build_controls_panel(main)

    def _build_preview_panel(self, parent):
        left = tk.LabelFrame(parent, text=f"PDF Preview ({RENDER_DPI} dpi)", width=_PREVIEW_W + 22)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        self.preview_canvas = tk.Canvas(left, width=_PREVIEW_W, bg="#dcdcdc")
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
        self.sender_var = tk.StringVar(master=self, value=self.settings.sender_email)
        tk.Entry(right, textvariable=self.sender_var).pack(fill="x", **pad)

        # Recipients listbox
        tk.Label(right, text="Recipients:", anchor="w").pack(fill="x", **pad)

        rec_list_frame = tk.Frame(right)
        rec_list_frame.pack(fill="x", **pad)
        self.recipient_listbox = tk.Listbox(rec_list_frame, height=3, selectmode=tk.EXTENDED)
        self.recipient_listbox.pack(side="left", fill="both", expand=True)
        for address in DEFAULT_RECIPIENTS:
            self.recipient_listbox.insert(tk.END, address)
        rec_scroll = tk.Scrollbar(rec_list_frame, orient="vertical", command=self.recipient_listbox.yview)
        rec_scroll.pack(side="right", fill="y")
        self.recipient_listbox.config(yscrollcommand=rec_scroll.set)

        rec_add_frame = tk.Frame(right)
        rec_add_frame.pack(fill="x", **pad)
        self.new_recipient_var = tk.StringVar(master=self)
        rec_entry = tk.Entry(rec_add_frame, textvariable=self.new_recipient_var)
        rec_entry.pack(side="left", fill="x", expand=True)
        rec_entry.bind("<Return>", lambda _e: self._add_recipient())
        tk.Button(rec_add_frame, text="Add", command=self._add_recipient).pack(side="left", padx=(4, 0))
        tk.Button(rec_add_frame, text="Remove Selected", command=self._remove_recipient).pack(side="left", padx=(4, 0))

        # PDF list
        tk.Label(right, text="PDFs to send  (click a file to preview):", anchor="w").pack(fill="x", **pad)

        pdf_btn_row = tk.Frame(right)
        pdf_btn_row.pack(fill="x", **pad)
        tk.Button(pdf_btn_row, text="Add PDFs…", command=self._add_pdfs).pack(side="left")
        self.remove_btn = tk.Button(pdf_btn_row, text="Remove Selected", command=self._remove_selected)
        self.remove_btn.pack(side="left", padx=(8, 0))
        self.clear_btn = tk.Button(pdf_btn_row, text="Clear All", command=self._clear_pdfs)
        self.clear_btn.pack(side="left", padx=(8, 0))

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
    # Startup
    # -------------------------------------------------------------------------

    def _log_startup(self, log_path: str | None):
        settings = self.settings
        if settings.env_files:
            log.info("Settings: %s", "; ".join(settings.env_files))
        else:
            log.info("Settings: from environment variables (no .env file found)")
        log.info("SourceImages folder: %s", settings.source_images_dir)
        for warning in settings.warnings:
            log.warning("Settings: %s", warning)
        if log_path:
            log.info("Log file: %s", log_path)
        else:
            log.warning("Couldn't create the log file, so messages only appear here.")

    def _check_ocr_tools(self, check_tools: Callable[[], OcrTools]):
        """Worker thread: look for Tesseract and Poppler without holding up startup."""
        try:
            tools = check_tools()
        except Exception:
            log.exception("Couldn't check for Tesseract and Poppler")
            return
        self.ui.post(self._report_ocr_tools, tools)

    def _report_ocr_tools(self, tools: OcrTools):
        if tools.can_ocr:
            log.info("OCR ready (Tesseract %s).\n", tools.tesseract_version)
            return
        for problem in tools.problems:
            log.warning("OCR: %s", problem)
        if tools.can_render:
            effect = ("PDF previews work, but booking numbers can't be read automatically, "
                      "so every PDF will need its booking number typed in.")
        else:
            effect = "PDF previews and OCR won't work, so every PDF will need its booking number typed in."
        details = "\n".join(f"• {problem}" for problem in tools.problems)
        messagebox.showwarning("OCR isn't available", f"{effect}\n\n{details}", parent=self)

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
            parent=self,
            title="Select PDF files",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        known = {_path_key(job.path) for job in self.jobs}
        first_new = None
        for path in files:
            if _path_key(path) in known:
                continue
            known.add(_path_key(path))
            job = PdfJob(path)
            self.jobs.append(job)
            if first_new is None:
                first_new = job

        if first_new is not None:
            self.listbox.selection_clear(0, tk.END)
            self.refresh_job_list(select=first_new)
            self._show_preview(first_new)

    def _remove_selected(self):
        for index in sorted(self.listbox.curselection(), reverse=True):
            del self.jobs[index]
        self.listbox.selection_clear(0, tk.END)
        self.refresh_job_list()

    def _clear_pdfs(self):
        self.jobs.clear()
        self.listbox.selection_clear(0, tk.END)
        self.refresh_job_list()

    def refresh_job_list(self, select: PdfJob | None = None):
        """Redraw the PDF list from self.jobs. Finished PDFs leave the list."""
        selected = [self.jobs[i] for i in self.listbox.curselection() if i < len(self.jobs)]
        self.jobs = [job for job in self.jobs if not job.done]
        self.listbox.delete(0, tk.END)
        for job in self.jobs:
            self.listbox.insert(tk.END, job.list_label())
        for job in [select] if select is not None else selected:
            if job in self.jobs:  # PdfJob compares by identity
                index = self.jobs.index(job)
                self.listbox.selection_set(index)
                self.listbox.see(index)
        if self._previewed_job is not None and self._previewed_job not in self.jobs:
            self._reset_preview()

    def _update_buttons(self):
        reviewing = self._review is not None
        busy = self._batch_running or reviewing
        if self._batch_running:
            text = "Sending…"
        elif reviewing:
            text = "Send Emails (finish Manual Booking Entry first)"
        else:
            text = "Send Emails"
        self.send_btn.config(state="disabled" if busy else "normal", text=text)
        for button in (self.remove_btn, self.clear_btn):
            button.config(state="disabled" if busy else "normal")

    # -------------------------------------------------------------------------
    # PDF preview
    # -------------------------------------------------------------------------

    def _on_pdf_select(self, _event=None):
        selection = self.listbox.curselection()
        if selection:
            self._show_preview(self.jobs[selection[-1]])

    def _canvas_size(self) -> tuple[int, int]:
        self.update_idletasks()
        return (
            max(self.preview_canvas.winfo_width(), _PREVIEW_W),
            max(self.preview_canvas.winfo_height(), _PREVIEW_H),
        )

    def _show_preview(self, job: PdfJob):
        self._previewed_job = job
        self._preview_seq += 1
        seq = self._preview_seq
        width, height = self._canvas_size()
        self._preview_status.config(text="Rendering…")
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(width // 2, height // 2, text="Loading…", fill="gray")
        self._preview_pool.submit(self._preview_worker, seq, job.path, width - 10, height - 10)

    def _preview_worker(self, seq: int, path: str, max_width: int, max_height: int):
        """Preview thread: show page 1, reusing a cached render when there is one."""
        if seq != self._preview_seq:
            return  # another PDF has been selected since

        def on_preview(image, error):
            fitted = fit_image(image, max_width, max_height) if image is not None else None
            self.ui.post(self._apply_preview, seq, fitted, error)

        try:
            # After the preview is up, OCR runs on the same render while this
            # PDF stays selected, so sending it later doesn't redo the work.
            self.analyzer.read(path, on_preview=on_preview, keep_going=lambda: seq == self._preview_seq)
        except Exception:
            log.exception("Couldn't preview %s", path)
            self.ui.post(self._apply_preview, seq, None, "Something went wrong. Details are in the log file.")

    def _apply_preview(self, seq: int, image, error: str):
        if seq != self._preview_seq:
            return
        width, height = self._canvas_size()
        self.preview_canvas.delete("all")
        self._preview_status.config(text="")
        if image is not None and ImageTk is not None:
            self._preview_photo = ImageTk.PhotoImage(image, master=self)  # Tk objects: main thread only
            self.preview_canvas.create_image(width // 2, height // 2, anchor="center", image=self._preview_photo)
        else:
            reason = error or "Pillow is installed without Tk support."
            self.preview_canvas.create_text(
                width // 2, height // 2,
                text=f"Preview unavailable\n\n{reason}",
                fill="gray", justify="center", width=width - 40,
            )

    def _reset_preview(self):
        self._preview_seq += 1  # drop any preview still on its way
        self._previewed_job = None
        width, height = self._canvas_size()
        self.preview_canvas.delete("all")
        self.preview_canvas.create_text(
            width // 2, height // 2, text="Select a PDF to preview", fill="gray", justify="center",
        )
        self._preview_status.config(text="")
        self._preview_photo = None

    # -------------------------------------------------------------------------
    # Log box (main thread; worker threads log through UiLogHandler)
    # -------------------------------------------------------------------------

    def _append_log(self, message: str):
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, message + "\n")
        self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    # -------------------------------------------------------------------------
    # CRM login, on behalf of worker threads
    # -------------------------------------------------------------------------

    def credential_prompt(self, parent: tk.Misc) -> AskCredentials:
        """Return a function a worker thread can call to ask the user for CRM
        credentials. The login dialog opens on the main thread, over parent."""

        def ask(message: str | None) -> CrmCredentials | None:
            answer: queue.Queue = queue.Queue(maxsize=1)
            self.ui.post(self._ask_crm_credentials, parent, message, answer)
            return answer.get()

        return ask

    def _ask_crm_credentials(self, parent: tk.Misc, message: str | None, answer: queue.Queue):
        credentials = None
        try:
            owner = parent if parent.winfo_exists() else self
            credentials = LoginDialog.ask(owner, username=self._crm_username, message=message)
            if credentials is not None:
                self._crm_username = credentials.username
        except Exception:
            log.exception("The CRM login window failed")
        finally:
            answer.put(credentials)

    # -------------------------------------------------------------------------
    # Send logic
    # -------------------------------------------------------------------------

    def _on_send(self):
        sender = self.sender_var.get().strip()
        recipients = list(self.recipient_listbox.get(0, tk.END))

        if not sender:
            messagebox.showwarning("Missing info", "Sender email is empty.", parent=self)
            return
        if not recipients:
            messagebox.showwarning("Missing info", "Add at least one recipient.", parent=self)
            return
        jobs = [job for job in self.jobs if not job.done]
        if not jobs:
            messagebox.showwarning("No PDFs", "Add at least one PDF file.", parent=self)
            return

        self._batch_running = True
        self._update_buttons()
        threading.Thread(
            target=self._run_batch, args=(jobs, sender, recipients), name="batch", daemon=True
        ).start()

    def _run_batch(self, jobs: list[PdfJob], sender: str, recipients: list[str]):
        """Worker thread: run each PDF's remaining steps, then hand the ones
        that need attention to the Manual Booking Entry window."""
        needs_attention: list[PdfJob] = []
        try:
            log.info("Signing in to Microsoft Graph…")
            try:
                self.mailer.check_credentials()
            except GraphAuthError as exc:
                log.error("Microsoft Graph sign-in failed, so nothing was sent. %s", exc)
                return
            ask = self.credential_prompt(self)
            try:
                self.pipeline.ensure_crm_login(ask)
            except CrmLoginCancelled:
                log.warning("CRM login cancelled — nothing sent.")
                return
            log.info("Recipients: %s\n", ", ".join(recipients))

            finished = 0
            for number, job in enumerate(jobs, 1):
                log.info("[%d/%d] %s", number, len(jobs), job.name)
                was_emailed = job.emailed
                try:
                    self.pipeline.run(job, sender=sender, recipients=recipients, ask_credentials=ask)
                    finished += 1
                except CrmLoginCancelled:
                    left = len(jobs) - number + 1
                    log.warning(
                        "\nStopped. %d PDF(s) weren't finished and are still in the list. "
                        "Click Send Emails to carry on.", left,
                    )
                    needs_attention.clear()
                    return
                except Exception:
                    needs_attention.append(job)  # the pipeline has already logged why
                finally:
                    self.ui.post(self.refresh_job_list)
                if number < len(jobs) and job.emailed and not was_emailed:
                    time.sleep(1)  # pace the emails

            log.info("\nDone. Finished: %d, need attention: %d", finished, len(needs_attention))
            for job in needs_attention:
                log.info("  • %s: %s", job.name, job.error)
        finally:
            self.ui.post(self._batch_finished, needs_attention, sender, recipients)

    def _batch_finished(self, needs_attention: list[PdfJob], sender: str, recipients: list[str]):
        self._batch_running = False
        self.refresh_job_list()
        if needs_attention:
            self._review = ManualReviewWindow(self, needs_attention, sender, recipients)
        self._update_buttons()

    def review_closed(self):
        self._review = None
        self.refresh_job_list()
        self._update_buttons()


class ManualReviewWindow(tk.Toplevel):
    """Goes through the PDFs a batch couldn't finish, one at a time.

    If the booking number couldn't be read, or didn't match a CRM booking,
    the user types it in. This is the only place a booking number is entered.
    If the booking was found but a later step failed, Retry runs only the
    steps that haven't finished. Skipped PDFs stay in the main list.
    """

    def __init__(self, app: SendPDFApp, jobs: list[PdfJob], sender: str, recipients: list[str]):
        super().__init__(app)
        self.app = app
        self.queue: list[PdfJob] = list(jobs)
        self.total = len(jobs)
        self.sender = sender
        self.recipients = recipients
        self._working = False
        self.title("Manual Booking Entry")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build_ui()
        self._show_current()

    # -- UI --------------------------------------------------------------

    def _build_ui(self):
        main = tk.Frame(self)
        main.pack(fill="both", expand=True, padx=12, pady=8)

        self._pad = pad = {"padx": 4, "pady": 3}
        self.progress_lbl = tk.Label(main, text="", font=("TkDefaultFont", 10, "bold"))
        self.progress_lbl.pack(fill="x", **pad)
        self.file_lbl = tk.Label(
            main, text="", wraplength=420, justify="left",
            fg="blue", cursor="hand2", font=("TkDefaultFont", 9, "underline"),
        )
        self.file_lbl.pack(fill="x", **pad)
        self.file_lbl.bind("<Button-1>", lambda _e: self._open_pdf())

        # What's already done (shown when only later steps need retrying)
        self.detail_lbl = tk.Label(main, text="", wraplength=420, justify="left", anchor="w")

        # Booking number entry (shown when the booking number is needed)
        self.booking_frame = tk.Frame(main)
        tk.Label(self.booking_frame, text="Booking #:", anchor="w").pack(fill="x", **pad)
        self.booking_var = tk.StringVar(master=self)
        self.booking_entry = tk.Entry(self.booking_frame, textvariable=self.booking_var)
        self.booking_entry.pack(fill="x", **pad)
        self.booking_entry.bind("<Return>", lambda _e: self._submit())

        self.submit_btn = tk.Button(main, text="Look Up & Send", command=self._submit, height=2)
        self.submit_btn.pack(fill="x", **pad)
        self.skip_btn = tk.Button(main, text="Skip This PDF", command=self._skip)
        self.skip_btn.pack(fill="x", **pad)

        self.status_lbl = tk.Label(main, text="", wraplength=420, justify="left", fg="gray", anchor="w")
        self.status_lbl.pack(fill="x", **pad)

    def _show_current(self):
        if not self.queue:
            log.info("\nManual entry complete.")
            self._finish()
            return

        job = self.queue[0]
        done = self.total - len(self.queue)
        self.progress_lbl.config(text=f"PDF {done + 1} of {self.total}")
        self.file_lbl.config(text=job.name)

        if job.needs_booking:
            self.detail_lbl.pack_forget()
            self.booking_frame.pack(fill="x", before=self.submit_btn)
            self.booking_var.set(job.booking)  # what OCR read, if anything
            self.booking_entry.select_range(0, tk.END)
            self.booking_entry.icursor(tk.END)
            self.submit_btn.config(text="Look Up & Send")
            hint = "Click the file name to open the PDF, then enter its booking number."
            self.status_lbl.config(text=f"{job.error}\n{hint}" if job.error else hint, fg="gray")
            self.booking_entry.focus_set()
        else:
            self.booking_frame.pack_forget()
            self.detail_lbl.config(text="\n".join(job.progress_lines()))
            self.detail_lbl.pack(fill="x", before=self.submit_btn, **self._pad)
            self.submit_btn.config(text="Retry")
            if job.email_maybe_sent and not job.emailed:
                self.status_lbl.config(
                    text="⚠ The last attempt may already have sent this email. "
                         "Check Sent Items before you retry.",
                    fg="firebrick",
                )
            else:
                self.status_lbl.config(text=job.error, fg="gray")
            self.submit_btn.focus_set()

    def _open_pdf(self):
        if self.queue:
            webbrowser.open(Path(self.queue[0].path).resolve().as_uri())

    def _set_working(self, working: bool):
        self._working = working
        state = "disabled" if working else "normal"
        for widget in (self.submit_btn, self.skip_btn, self.booking_entry):
            widget.config(state=state)

    # -- Actions ----------------------------------------------------------

    def _submit(self):
        if self._working or not self.queue:
            return
        job = self.queue[0]
        if job.needs_booking:
            booking = "".join(self.booking_var.get().split()).upper()
            if not booking:
                messagebox.showwarning("Missing info", "Enter a booking number.", parent=self)
                return
            if booking != job.booking:
                job.booking = booking
                job.booking_from_ocr = False
        elif job.email_maybe_sent and not job.emailed:
            if not messagebox.askyesno(
                "Send the email again?",
                "The last attempt may already have sent this email. Check Sent Items first.\n\n"
                "Send it again?",
                parent=self,
            ):
                return
            job.email_maybe_sent = False

        self._set_working(True)
        self.status_lbl.config(text="Working…", fg="gray")
        threading.Thread(target=self._submit_worker, args=(job,), name="manual", daemon=True).start()

    def _submit_worker(self, job: PdfJob):
        if job.needs_booking:
            log.info("[manual] %s — booking %s", job.name, job.booking)
        else:
            log.info("[manual] %s — retrying", job.name)
        try:
            self.app.pipeline.run(
                job,
                sender=self.sender,
                recipients=self.recipients,
                ask_credentials=self.app.credential_prompt(self),
            )
        except Exception:
            self.app.ui.post(self._after_attempt, False)  # the pipeline has already logged why
        else:
            self.app.ui.post(self._after_attempt, True)

    def _after_attempt(self, succeeded: bool):
        self._set_working(False)
        if succeeded:
            self.queue.pop(0)
        self.app.refresh_job_list()
        self._show_current()

    def _skip(self):
        if self._working or not self.queue:
            return
        job = self.queue.pop(0)
        log.info("[manual] Skipped %s. It's still in the list.", job.name)
        self._show_current()

    def _close(self):
        if self._working:
            self.bell()  # wait for the current PDF to finish
            return
        if self.queue:
            log.info("[manual] Closed with %d PDF(s) left. They're still in the list.", len(self.queue))
        self._finish()

    def _finish(self):
        self.destroy()
        self.app.review_closed()
