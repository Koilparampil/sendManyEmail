# Validated Titles Sender

A Windows desktop tool for processing validated-title PDFs. For each PDF it:

1. reads the booking number off the first page with OCR,
2. looks the booking up in VShip CRM,
3. files the PDF in the SourceImages library as `<reference>_VAL.pdf`,
4. emails it through Microsoft Graph, and
5. adds a note to the booking.

Any PDF it can't handle automatically goes to a manual-entry window, where you type the booking number yourself.

## How it works

For every PDF in the list, **Send Emails** runs this pipeline (`crm_pipeline.process_pdf`):

1. **OCR.** Renders page 1 at 600 DPI with Poppler (`pdf2image`) and reads it with Tesseract (`pytesseract`).
2. **Find the booking number.** Patterns are tried in this order, and the first match wins:
   - `EBKG` + 8 digits (one stray OCR space allowed)
   - `NAM` + 7 digits
   - `S3` + 8 digits
   - the first run of 7–12 digits within ~200 characters after a `BOOKING NO` label
3. **Look it up in the CRM.** Searches VShip CRM for the booking and takes the booking ID, reference number, and customer name from the closest match.
4. **File it.** Copies the PDF into the SourceImages folder as `<reference>_VAL.pdf`. It first deletes any existing `<reference>_VAL.pdf` and numbered copies such as `<reference>_VAL2.pdf`.
5. **Email it.** Sends the saved copy from the **Sender** mailbox to every recipient with Graph `sendMail`. The subject is `Validated Titles MM.DD.YYYY - <booking>`, and a copy is kept in Sent Items.
6. **Note it.** Adds a comment to the booking in VShip CRM saying the VALs were received, sent, and saved. The comment includes the file name.

If no booking number is found, or any later step fails, the PDF is queued. When the batch finishes, the **Manual Booking Entry** window opens and goes through the queue one PDF at a time. Click the file name to open the PDF, then either type its booking number and press **Enter** to run steps 3–6, or click **Skip This PDF**.

## Requirements

- **Windows 10/11.** File paths, the maximized window, and the build all assume Windows.
- **Python 3.10 or newer**, with Tkinter (included in the python.org installer).
- **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)** (UB Mannheim build).
- **[Poppler for Windows](https://github.com/oschwartz10612/poppler-windows/releases)**.
- **A Microsoft Entra ID app registration** with permission to send mail from the Sender mailbox (see [step 3](#3-register-the-azure-app)).
- **A VShip CRM account.** The app asks for your username and password and never stores the password.
- **The SourceImages SharePoint library, synced with OneDrive** to the path in `SOURCE_IMAGES_DIR` (set in `crm_pipeline.py`, under `%USERPROFILE%\VShip\`).

## Setup

### 1. Install the Python packages

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install msal requests python-dotenv pdf2image pillow pytesseract
```

### 2. Install Tesseract and Poppler

Install both, then make sure the app can find them. You can add these folders to `PATH`:

- `C:\Program Files\Tesseract-OCR`
- Poppler's `Library\bin` folder (for example `C:\Program Files\poppler\Library\bin`)

Or copy them into the project folder. This is the same layout the built `.exe` uses:

```text
tesseract\tesseract.exe   <- contents of C:\Program Files\Tesseract-OCR
poppler\pdftoppm.exe      <- contents of Poppler's Library\bin
```

`ocr_utils.py` looks in those two folders first, then falls back to `PATH`.

### 3. Register the Azure app

1. In the Microsoft Entra admin center, go to **App registrations → New registration**. A single-tenant app is enough.
2. Go to **API permissions → Add a permission → Microsoft Graph → Application permissions**, add **`Mail.Send`**, then click **Grant admin consent**.
3. Go to **Certificates & secrets → New client secret**. Copy the secret's **Value**, not its Secret ID; it goes in `VALUE`.
4. On **Overview**, copy the **Application (client) ID** and the **Directory (tenant) ID**.

> **Limit what the secret can do.** The application `Mail.Send` permission lets the app send as *any* mailbox in the tenant. To restrict it to the sending mailbox, use Exchange Online [RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac) with a scope that covers only that mailbox. In that case, grant `Mail.Send` through the Exchange role assignment *instead of* the Entra permission in step 2. An Entra grant still applies on top of an RBAC scope.

### 4. Create `.env`

```dotenv
# Required
APP_CLIENT_ID=00000000-0000-0000-0000-000000000000
DIRECTORY_TENANT_ID=00000000-0000-0000-0000-000000000000
VALUE=your-client-secret-value

# Optional
SENDER_EMAIL=titles@yourdomain.com
SENDER=your.crm.username
APP_CLIENT_ID-2=
VALUE-2=
```

| Variable | Required | Purpose |
|---|---|---|
| `APP_CLIENT_ID` | yes | Application (client) ID of the app registration |
| `DIRECTORY_TENANT_ID` | yes | Directory (tenant) ID |
| `VALUE` | yes | Client secret **value** |
| `SENDER_EMAIL` | no | Pre-fills the **Sender** field, which is the mailbox mail is sent from |
| `SENDER` | no | Pre-fills the username in the CRM login dialog |
| `APP_CLIENT_ID-2`, `VALUE-2` | no | A second app registration in the same tenant. If a send is throttled (HTTP 429), it's retried once with these credentials |
| `SECRET_ID`, `SECRET_ID-2` | no | Read at startup but not used |

Where the file goes:

- **Running from source:** the project root, next to `mainIG.py`.
- **Built `.exe`:** next to `ValidatedTitlesSender.exe`. A `.env` there takes precedence over the copy bundled into the build.

> `SENDER_EMAIL` and `SENDER` are read before `mainIG.py` loads its `.env`. They only take effect from a `.env` next to the `.exe`, or when set as real environment variables. When you run from source, type them into the app instead.

`.env` is listed in `.gitignore`. Never commit it.

## Running

```powershell
python mainIG.py
```

1. Check the **Sender** mailbox and the **Recipients** list. One default recipient is hard-coded in `gui.py`. Use **Add** and **Remove Selected** to change the list.
2. Click **Add PDFs…** and pick one or more files. Click a file in the list to preview its first page. The booking number OCR finds appears under **Booking #**. That field is display-only; see [Known limitations](#known-limitations).
3. Click **Send Emails**. If no CRM token has been saved in the last hour, a **Booking Tool Login** dialog asks for your VShip CRM credentials.
4. Watch the log. For each PDF it shows:
   - the booking number OCR found
   - the CRM reference and customer
   - the saved file name
   - whether the email and the CRM note succeeded
5. If any PDFs were queued, the **Manual Booking Entry** window opens when the batch finishes.

**Re-extract OCR** re-runs OCR on the selected PDF and writes the raw OCR text to the log. Use it when a booking number isn't being picked up.

Sending pauses 1 second between PDFs. A send that fails with a network error is tried up to 3 times in total, waiting 10 s and then 20 s. A throttled send (HTTP 429) switches once to the backup credentials, if they're configured. Any other HTTP error queues the PDF for manual entry.

## Building the `.exe`

Install PyInstaller (`pip install pyinstaller`). Then run the command from [`pyInstaller command`](pyInstaller%20command) in PowerShell, from the project root:

```powershell
pyinstaller --noconfirm --clean --onedir --windowed --name ValidatedTitlesSender `
  --paths . `
  --add-data ".env;." `
  --add-data "C:\Program Files\Tesseract-OCR;tesseract" `
  --add-data "C:\Program Files\poppler\Library\bin;poppler" `
  --workpath "C:\Temp\VShipBuild\build" `
  --distpath "D:\sendEmail" `
  mainIG.py
```

This produces `D:\sendEmail\ValidatedTitlesSender\ValidatedTitlesSender.exe`, with Tesseract and Poppler bundled in. Change the Tesseract and Poppler source paths and `--distpath` to match your machine.

> **Warning:** `--add-data ".env;."` copies the client secret into the output folder as plain text. Anyone with a copy of that folder can send mail as the app. It's safer to leave that line out and put a `.env` next to the `.exe` on each machine. That also lets you change settings without rebuilding.

## Project layout

| Path | What it does |
|---|---|
| `mainIG.py` | Entry point. Loads `.env`, checks the required settings, hides console windows for child processes on Windows, and starts the GUI |
| `gui.py` | Tkinter UI: the main window (`SendPDFApp`) and the Manual Booking Entry window |
| `crm_pipeline.py` | The per-PDF pipeline (look up → save → email → note), the CRM login dialog, and `SOURCE_IMAGES_DIR` |
| `ocr_utils.py` | Page rendering, OCR, booking-number patterns, and preview scaling |
| `auth.py` | Gets an app-only Microsoft Graph token (MSAL client credentials) |
| `email_sender.py` | Sends the PDF with Graph `sendMail`, including retries and the 429 fallback |
| `tinkyWinky_log_only.py` | Standalone CRM login dialog, `UserInputs`, and the `SENDER` setting |
| `VShipCRM/signInVShipCRM.py` | CRM login and the cached token file |
| `VShipCRM/ref_lookup.py` | Booking search and best-match selection |
| `VShipCRM/note_ting/make_VShip_notes.py` | Adds the comment to a CRM booking |
| `email_ting/` | **Not imported by this app.** Left over from another tool; `util_sharepoint_help.py` only parses on Python 3.12+ |
| `pyInstaller command` | The build command above |

## Files the app writes

- **`auth_for_VshipCRM.txt`**: the CRM bearer token. It's written to the *current working directory* and reused for up to an hour. Delete it to force a fresh login. The `*.txt` rule in `.gitignore` keeps it out of git.
- **`<reference>_VAL.pdf`** in `SOURCE_IMAGES_DIR`: one per processed PDF. Older copies for the same reference are deleted.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| The built `.exe` doesn't open at all | A required `.env` value is missing. The error goes to a console window, which `--windowed` builds don't have. Check the `.env` next to the `.exe` |
| Every PDF goes to Manual Booking Entry with "No booking number found" | Tesseract or Poppler wasn't found. Select a PDF and click **Re-extract OCR**; the log shows the OCR error. "(OCR ready)" only means the Python packages imported. It doesn't check for `tesseract.exe` or Poppler |
| "Preview unavailable" or "Could not render this PDF" | Poppler isn't installed or wasn't found, or the PDF is encrypted or damaged |
| "CRM login failed — check username/password." | Wrong credentials, or the CRM rejected the login. Delete `auth_for_VshipCRM.txt` and try again |
| Graph HTTP 401 in the Manual Booking Entry window | The app fetches one Graph token per batch, and it expires after about an hour. Close the window, remove the PDFs that were already sent, and send the rest again |
| Graph HTTP 403 | The app doesn't have admin-consented `Mail.Send`, or it isn't allowed to send from the Sender mailbox |
| Graph HTTP 429 | Microsoft is throttling the app. Configure the backup credentials (`APP_CLIENT_ID-2` and `VALUE-2`), or wait and try again |

## Known limitations

- **The Booking # field is display-only.** Editing it has no effect, because **Send Emails** runs OCR again on every PDF and uses that result. After each batch, check the `Booking # (OCR)` and `CRM: ref …` lines in the log.
- **Retrying in Manual Booking Entry re-runs every step, including the email.** If only the CRM note failed, the recipients get the email twice.
- **A CRM token that expires mid-batch can hang the app.** The re-login prompt is opened from a background thread. Before a large batch, delete `auth_for_VshipCRM.txt` so the batch starts with a full hour.
- **Some values are hard-coded.** The default recipient is in `gui.py`. The CRM note text, which names that recipient, and `SOURCE_IMAGES_DIR`, which includes the year, are in `crm_pipeline.py`.
- **Saving replaces existing files.** All `<reference>_VAL*.pdf` copies for a reference are deleted before the new one is written.
- **Windows only.**
