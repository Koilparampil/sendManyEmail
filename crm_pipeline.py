"""The per-PDF pipeline: OCR -> CRM lookup -> save to SourceImages -> email -> CRM note.

Each PdfJob remembers which steps have finished, so running it again only
does what's left. That's what stops a retry from emailing a PDF twice.
Used by both the batch (Send Emails) and the Manual Booking Entry window.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from email_sender import EmailError, EmailMaybeSent, GraphMailer
from graph_auth import GraphAuthError
from ocr_utils import PageAnalyzer
from vship_crm import (
    BookingMatch,
    CrmAuthRequired,
    CrmClient,
    CrmCredentials,
    CrmError,
    CrmLoginFailed,
)

log = logging.getLogger(__name__)

NOTE_TEXT = "VALS RECEIVED, SENT TO AAKASH, SAVED IN SOURCE IMAGES AS {filename}"

# Called from a worker thread to get CRM credentials from the user. The
# argument is a message to show (why they're being asked again), or None.
# Returns None if the user cancels.
AskCredentials = Callable[[str | None], CrmCredentials | None]


class PipelineError(Exception):
    """A step failed in an expected way. The message says why."""


class NoBookingNumber(PipelineError):
    """OCR didn't find a booking number."""


class CrmLoginCancelled(PipelineError):
    """The user cancelled the CRM login."""


_EXPECTED_ERRORS = (PipelineError, CrmError, EmailError, GraphAuthError, OSError)


@dataclass(eq=False)  # jobs are compared by identity
class PdfJob:
    """One PDF and how far it has got through the pipeline."""

    path: str
    booking: str = ""  # booking number to look up ("" until OCR or the user supplies one)
    booking_from_ocr: bool = False
    match: BookingMatch | None = None  # set once the CRM lookup succeeds
    saved_as: str | None = None  # path in SourceImages, once saved
    emailed: bool = False
    email_maybe_sent: bool = False  # last send attempt ended without a clear answer
    noted: bool = False
    error: str = ""  # why the last attempt stopped

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def needs_booking(self) -> bool:
        return self.match is None

    @property
    def done(self) -> bool:
        return self.noted

    def status(self) -> str:
        """A short status for the main list ("" for a PDF that hasn't been tried)."""
        if self.done:
            return "done"
        if self.match is None:
            if not self.error:
                return ""
            return "CRM lookup failed" if self.booking else "no booking # found"
        if self.saved_as is None:
            return "found in CRM, not saved yet"
        if not self.emailed:
            return "email may have been sent" if self.email_maybe_sent else "saved, not emailed yet"
        return "emailed, CRM note missing"

    def list_label(self) -> str:
        status = self.status()
        return f"{self.name}    [{status}]" if status else self.name

    def progress_lines(self) -> list[str]:
        """What has and hasn't been done, for the Manual Booking Entry window."""
        lines = []
        if self.match is not None:
            booking = self.booking if self.match.exact else f"{self.booking} (CRM booking {self.match.booking_no})"
            customer = f" ({self.match.customer_name})" if self.match.customer_name else ""
            lines.append(f"Booking {booking} → CRM ref {self.match.reference_no}{customer}")
        lines.append(f"✓ Saved as {os.path.basename(self.saved_as)}" if self.saved_as else "✗ Not saved yet")
        if self.emailed:
            lines.append("✓ Emailed")
        else:
            lines.append("? The email may have been sent" if self.email_maybe_sent else "✗ Not emailed yet")
        lines.append("✓ CRM note added" if self.noted else "✗ CRM note not added yet")
        return lines


def save_val_copy(pdf_path: str, reference_no: str, folder: str) -> str:
    """Copy pdf_path into folder as '<reference_no>_VAL.pdf' and return its path.

    The copy goes to a temporary file first, which then replaces
    '<reference_no>_VAL.pdf' in one step, so a failure leaves any existing
    file untouched. Only after that are old numbered copies
    ('<reference_no>_VAL2.pdf' and so on) deleted.
    """
    name = f"{reference_no}_VAL.pdf"
    dest = os.path.join(folder, name)
    replacing = os.path.exists(dest)
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix=f"~{reference_no}_VAL.", suffix=".tmp", dir=folder)
        os.close(fd)
        shutil.copy2(pdf_path, temp_path)
        os.replace(temp_path, dest)
    except OSError as exc:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                os.remove(temp_path)
        raise PipelineError(f"Couldn't save {name} in SourceImages: {exc}") from exc
    if replacing:
        log.info("  Replaced the existing %s", name)

    numbered = re.compile(rf"^{re.escape(reference_no)}_VAL\d+\.pdf$", re.IGNORECASE)
    for other in sorted(os.listdir(folder)):
        if numbered.match(other):
            try:
                os.remove(os.path.join(folder, other))
                log.info("  Removed old copy %s", other)
            except OSError as exc:
                log.warning("  Couldn't remove old copy %s: %s", other, exc)
    return dest


class Pipeline:
    def __init__(
        self,
        *,
        crm: CrmClient,
        mailer: GraphMailer,
        analyzer: PageAnalyzer,
        source_images_dir: str,
    ):
        self.crm = crm
        self.mailer = mailer
        self.analyzer = analyzer
        self.source_images_dir = source_images_dir

    def ensure_crm_login(self, ask_credentials: AskCredentials, message: str | None = None) -> None:
        """Make sure there's a fresh CRM token, asking the user if needed.

        Keeps asking until a login works. Raises CrmLoginCancelled.
        """
        if self.crm.has_fresh_token():
            return
        while True:
            credentials = ask_credentials(message)
            if credentials is None:
                raise CrmLoginCancelled("CRM login cancelled.")
            try:
                self.crm.sign_in(credentials.username, credentials.password)
                return
            except CrmLoginFailed as exc:
                log.warning("  %s", exc)
                message = str(exc)

    def run(
        self,
        job: PdfJob,
        *,
        sender: str,
        recipients: list[str],
        ask_credentials: AskCredentials,
    ) -> None:
        """Run whatever steps the job still needs. Call from a worker thread.

        On failure, job.error says why, the reason is logged, and the
        exception is re-raised. Steps that finished stay finished.
        """
        job.error = ""
        try:
            self._run_steps(job, sender, recipients, ask_credentials)
        except CrmLoginCancelled as exc:
            job.error = str(exc)
            log.warning("  ✗ %s", exc)
            raise
        except _EXPECTED_ERRORS as exc:
            job.error = str(exc)
            log.info("  ✗ %s", exc)
            raise
        except Exception as exc:
            job.error = f"Unexpected error: {exc}"
            log.exception("  ✗ Unexpected error: %s", exc)
            raise

    def _run_steps(self, job: PdfJob, sender: str, recipients: list[str], ask: AskCredentials) -> None:
        if job.match is None:
            if not job.booking:
                self._read_booking(job)
            job.match = self._with_crm_login(ask, lambda: self.crm.find_booking(job.booking))
            customer = f" — {job.match.customer_name}" if job.match.customer_name else ""
            log.info("  CRM: ref %s%s", job.match.reference_no, customer)
            if not job.match.exact:
                log.info("  (%s matched CRM booking %s, which isn't an exact match)", job.booking, job.match.booking_no)

        if job.saved_as is None:
            job.saved_as = save_val_copy(job.path, job.match.reference_no, self.source_images_dir)
            log.info("  Saved as %s", os.path.basename(job.saved_as))
        filename = os.path.basename(job.saved_as)

        if not job.emailed:
            if job.email_maybe_sent:
                raise PipelineError(
                    "The last attempt may already have sent this email, so it isn't sent again automatically. "
                    "Check Sent Items, then retry from the Manual Booking Entry window."
                )
            booking_no = job.match.booking_no.strip() or job.booking  # as the CRM has it
            subject = f"Validated Titles {datetime.now():%m.%d.%Y} - {booking_no}"
            try:
                self.mailer.send_pdf(sender, recipients, job.saved_as, subject)
            except EmailMaybeSent:
                job.email_maybe_sent = True
                raise
            job.emailed = True
            log.info("  ✓ Emailed %s to %d recipient(s)", filename, len(recipients))

        if not job.noted:
            note = NOTE_TEXT.format(filename=filename)
            self._with_crm_login(ask, lambda: self.crm.add_comment(job.match.booking_id, note))
            job.noted = True
            log.info("  ✓ CRM note added")

    def _read_booking(self, job: PdfJob) -> None:
        reading = self.analyzer.read(job.path)
        if reading.error:
            raise NoBookingNumber(f"Couldn't read page 1: {reading.error}")
        if not reading.booking:
            log.debug("OCR text of %s (no booking number found):\n%s", job.name, reading.text)
            raise NoBookingNumber("No booking number found on page 1. The OCR text is in the log file.")
        job.booking = reading.booking
        job.booking_from_ocr = True
        log.info("  Booking # (OCR): %s", job.booking)

    def _with_crm_login(self, ask: AskCredentials, call: Callable):
        """Run a CRM call, logging in first if needed and once more if the CRM rejects the token."""
        self.ensure_crm_login(ask)
        try:
            return call()
        except CrmAuthRequired:
            self.crm.forget_token()
            self.ensure_crm_login(ask, "VShip CRM rejected the saved login. Please log in again.")
            return call()
