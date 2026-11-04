# Validated Titles Sender

A Windows desktop tool for processing validated-title PDFs. For each PDF it:

1. reads the booking number off the first page with OCR,
2. looks the booking up in VShip CRM,
3. files the PDF in the SourceImages library as `<reference>_VAL.pdf`,
4. emails it through Microsoft Graph, and
5. adds a note to the booking.

Any PDF it can't finish automatically goes to a manual-entry window. That window is the only place you ever type a booking number.

## How it works

For every PDF in the list, **Send Emails** runs this pipeline (`crm_pipeline.py`):

1. **OCR.** Renders page 1 once, at 300 DPI in colour, with Poppler (`pdf2image`). The same render is used for the on-screen preview and for Tesseract (`pytesseract`). Results are cached per file, so a PDF you've already previewed isn't rendered or read again when it's sent.
2. **Find the booking number.**
   - **Labelled value first.** A value right after a `BOOKING NO` label wins, if it starts within about 200 characters of the label. It can be a carrier number (`EBKG` + 8 digits, `NAM` + 7 digits, `S3` + 8 digits) or a plain 7–12 digit number.
   - **Carrier format anywhere.** Otherwise, the first carrier-format number anywhere on the page is used.
   - **Standalone only.** A match must stand on its own: no letter or digit just before it, and no digit just after it. So container numbers, VINs and longer numbers aren't taken for booking numbers, and numbers are never cut short.
3. **Look it up in the CRM.** Searches VShip CRM and picks the booking:
   - **Copies are never used.** Copies such as `EBKG123_Copy_76099` are skipped.
   - **Exact match first.** An exact match wins, ignoring case, spaces and punctuation.
   - **Whole-number containment otherwise.** A booking whose number contains the one searched for, as a whole number, is accepted: `17093338` finds `EBKG17093338`, but never `170933381`. The log flags these matches so you can check them.
   - **Otherwise, manual entry.** If no booking qualifies, or more than one does, the PDF goes to manual entry.
4. **File it.** Copies the PDF into `SOURCE_IMAGES_DIR` as `<reference>_VAL.pdf`. It writes a temporary file first, then swaps it in, so a failed copy never costs you the existing file. Only after that are numbered copies such as `<reference>_VAL2.pdf` deleted.
5. **Email it.** Sends the saved copy from the **Sender** mailbox to every recipient with Graph `sendMail`. The subject is `Validated Titles MM.DD.YYYY - <booking>`, using the booking number as the CRM has it. A copy is kept in Sent Items.
6. **Note it.** Adds a comment to the booking in VShip CRM saying the VALs were received, sent, and saved. The comment includes the file name.

**Each PDF remembers which steps have finished.** Running it again, from **Retry** or **Send Emails**, only does what's left. So a PDF is never emailed twice just because a later step failed.

### Manual Booking Entry

When a batch finishes, this window goes through the PDFs that need attention, one at a time. Click the file name to open the PDF.

- **No booking number found, or no CRM match:** type the booking number and press **Enter**. If OCR read something, it's filled in, ready to correct.
- **Booking found but a later step failed:** the window shows what's done. **Retry** runs only the remaining steps.
- **The email may already have gone out:** this happens when the connection dropped after the email was handed to Microsoft. The window warns you and asks before sending it again. Check Sent Items first.
- **Skip This PDF** leaves the PDF in the main list, with its progress kept. Closing the window does the same for any PDFs left.

## Requirements

- **Windows 10/11.** File paths, the maximized window, and the build all assume Windows.
- **Python 3.10 or newer**, with Tkinter (included in the python.org installer).
- **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)** (UB Mannheim build).
- **[Poppler for Windows](https://github.com/oschwartz10612/poppler-windows/releases)**.
- **A Microsoft Entra ID app registration** with permission to send mail from the Sender mailbox (see [step 3](#3-register-the-azure-app)).
- **A VShip CRM account.** The app asks for your username and password and never stores the password.
- **The SourceImages SharePoint library, synced with OneDrive** to this PC. Its folder goes in `SOURCE_IMAGES_DIR`.

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

`ocr_utils.py` looks in those two folders first, then falls back to `PATH`. The app checks for both programs when it starts. If one is missing, it tells you which one.

### 3. Register the Azure app

1. In the Microsoft Entra admin center, go to **App registrations → New registration**. A single-tenant app is enough.
2. Go to **API permissions → Add a permission → Microsoft Graph → Application permissions**, add **`Mail.Send`**, then click **Grant admin consent**.
3. Go to **Certificates & secrets → New client secret**. Copy the secret's **Value**, not its Secret ID; it goes in `CLIENT_SECRET`.
4. On **Overview**, copy the **Application (client) ID** and the **Directory (tenant) ID**.

> **Limit what the secret can do.** The application `Mail.Send` permission lets the app send as *any* mailbox in the tenant. To restrict it to the sending mailbox, use Exchange Online [RBAC for Applications](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac) with a scope that covers only that mailbox. In that case, grant `Mail.Send` through the Exchange role assignment *instead of* the Entra permission in step 2. An Entra grant still applies on top of an RBAC scope.

### 4. Create `.env`

```dotenv
# Required
APP_CLIENT_ID=00000000-0000-0000-0000-000000000000
DIRECTORY_TENANT_ID=00000000-0000-0000-0000-000000000000
CLIENT_SECRET=your-client-secret-value
SOURCE_IMAGES_DIR=%USERPROFILE%\VShip\Your Library - 2026 SourceImages

# Optional
SENDER_EMAIL=titles@yourdomain.com
CRM_USERNAME=your.crm.username
APP_CLIENT_ID_2=
CLIENT_SECRET_2=
```

| Setting | Required | Purpose |
|---|---|---|
| `APP_CLIENT_ID` | yes | Application (client) ID of the app registration |
| `DIRECTORY_TENANT_ID` | yes | Directory (tenant) ID |
| `CLIENT_SECRET` | yes | Client secret **value** |
| `SOURCE_IMAGES_DIR` | yes | The synced SourceImages folder. `%USERPROFILE%` and other variables are expanded. The app won't start if the folder doesn't exist |
| `SENDER_EMAIL` | no | Pre-fills the **Sender** field, which is the mailbox mail is sent from |
| `CRM_USERNAME` | no | Pre-fills the username in the CRM login dialog |
| `APP_CLIENT_ID_2`, `CLIENT_SECRET_2` | no | A second app registration in the same tenant. If Microsoft throttles a send (HTTP 429), it's retried once with these credentials |

Leave Windows paths **unquoted**. Inside double quotes, sequences such as `\n` in `\new folder` are read as escape characters.

Where the file goes:

- **Running from source:** the project root, next to `main.py`.
- **Built `.exe`:** next to `ValidatedTitlesSender.exe`, or bundled into the build by `build.ps1`.

If a setting appears in more than one place, a real environment variable wins first, then the `.env` next to the `.exe`, then the bundled `.env`.

`.env` is listed in `.gitignore`. Never commit it.

### Upgrading from an older version

- **Add `SOURCE_IMAGES_DIR`.** The folder used to be hard-coded in `crm_pipeline.py`.
- **Rename the old settings** `VALUE`, `SENDER`, `APP_CLIENT_ID-2` and `VALUE-2` to `CLIENT_SECRET`, `CRM_USERNAME`, `APP_CLIENT_ID_2` and `CLIENT_SECRET_2`. The old names still work for now, but the log warns about them.
- **Delete `SECRET_ID` and `SECRET_ID-2`.** They were never used.
- **Use the new entry points.** Run `python main.py` instead of `mainIG.py`, and build with `build.ps1` instead of `pyInstaller command`.

## Running

```powershell
python main.py
```

If a required setting is missing, or `SOURCE_IMAGES_DIR` doesn't exist, a dialog lists every problem and where the app looked for `.env`.

1. Check the **Sender** mailbox and the **Recipients** list. One default recipient is hard-coded in `gui.py`. Use **Add** and **Remove Selected** to change the list.
2. Click **Add PDFs…** and pick one or more files. Click a file in the list to preview its first page.
3. Click **Send Emails**. If no CRM token has been saved in the last hour, a **Booking Tool Login** dialog asks for your VShip CRM credentials. If the CRM rejects the saved login, or the hour runs out partway through a batch, the dialog appears again and the batch carries on.
4. Watch the log. For each PDF it shows:
   - the booking number OCR found
   - the CRM reference and customer
   - the saved file name
   - whether the email and the CRM note succeeded
5. Finished PDFs leave the list. Any others stay in it with a status such as `[no booking # found]` or `[emailed, CRM note missing]`, and the **Manual Booking Entry** window opens. **Send Emails** stays disabled until you close that window.

### Retries and timeouts

**Microsoft Graph:**
- **Fresh token for every email.** A token is fetched before each send, so long batches and a Manual Booking Entry window left open don't run into expired tokens.
- **Throttling.** HTTP 429 switches once to the backup credentials, if they're configured. After that, 429 and 503 responses wait as long as Microsoft's `Retry-After` header asks, then retry. The app retries up to five times, and gives up if asked to wait more than five minutes.
- **No connection.** If no connection could be made at all, the send is tried up to 3 times in total, waiting 10 s and then 20 s.
- **Dropped connection.** If the connection drops after the email was handed over, it's never resent automatically, because it may already have gone out.
- **Pacing.** Sending pauses 1 second between emails.

**VShip CRM timeouts:**
- logging in: 10 s to connect and 30 s for an answer
- booking search: 10 s
- adding the note: 25 s

## Building the `.exe`

Install PyInstaller (`pip install pyinstaller`). Then run `build.ps1` in PowerShell:

```powershell
.\build.ps1
```

This produces `D:\sendEmail\ValidatedTitlesSender\ValidatedTitlesSender.exe`, with Tesseract and Poppler bundled in. The script checks that Tesseract, Poppler and PyInstaller can be found before it starts. You can change these parameters:

| Parameter | Default |
|---|---|
| `-TesseractDir` | `C:\Program Files\Tesseract-OCR` |
| `-PopplerBin` | `C:\Program Files\poppler\Library\bin` |
| `-DistPath` | `D:\sendEmail` |
| `-WorkPath` (PyInstaller's working folder and `.spec` file) | `C:\Temp\VShipBuild\build` |
| `-Name` | `ValidatedTitlesSender` |
| `-ExcludeEnv` | off: the project's `.env` is bundled, and the build stops if there isn't one |

> **Warning:** By default, `.env` is copied into the output folder as plain text, client secret included. Anyone with a copy of that folder can send mail as the app. It's safer to build with `-ExcludeEnv` and put a `.env` next to the `.exe` on each PC. That also lets you change settings without rebuilding.

## Project layout

| Path | What it does |
|---|---|
| `main.py` | Entry point. Sets up logging, loads the settings, shows startup problems in a dialog, starts the GUI |
| `config.py` | Reads `.env` files and the environment once, checks the settings |
| `app_logging.py` | Log file, console logging, and hooks that record errors the windowed `.exe` would otherwise lose |
| `gui.py` | Tkinter UI: the main window, the Manual Booking Entry window and the CRM login dialog |
| `crm_pipeline.py` | The per-PDF pipeline and its step tracking (`PdfJob`), and saving into SourceImages |
| `ocr_utils.py` | Page rendering, OCR, booking-number patterns, the render/OCR cache, and the Tesseract/Poppler check |
| `vship_crm.py` | VShip CRM client: login, booking search and matching, comments |
| `graph_auth.py` | App-only Microsoft Graph tokens (MSAL client credentials) |
| `email_sender.py` | Sends the PDF with Graph `sendMail`, with retries, throttling and the backup credentials |
| `build.ps1` | Builds the `.exe` with PyInstaller |

Only the Tk main thread touches windows. Background threads pass results back through a queue, and when one needs CRM credentials, the main thread shows the login dialog for it.

## Files the app writes

- **`auth_for_VshipCRM.txt`**: the CRM bearer token. It's written to the *current working directory* and reused for up to an hour. Delete it to force a fresh login. The `*.txt` rule in `.gitignore` keeps it out of git.
- **`<reference>_VAL.pdf`** in `SOURCE_IMAGES_DIR`: one per processed PDF.
- **`%LOCALAPPDATA%\ValidatedTitlesSender\logs\app.log`**: everything shown in the window's log. It also holds error details and the OCR text of any PDF whose booking number wasn't found. It's replaced at 1 MB, and five old logs are kept. The window's log shows the path at startup.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A "settings problem" dialog at startup | The dialog lists every missing or wrong setting, and which `.env` files were read |
| An "OCR isn't available" warning at startup | Tesseract or Poppler wasn't found. The warning says which one |
| "No booking number found on page 1" | OCR didn't find a booking number. The text OCR read is in the log file |
| "No CRM booking matches …" or "2 CRM bookings match …" | The number read from the PDF isn't in the CRM, or is ambiguous. Enter the right one in Manual Booking Entry |
| "CRM login failed" | Wrong username or password, or the CRM is unreachable. The login dialog asks again |
| Graph HTTP 403 | The app doesn't have admin-consented `Mail.Send`, or it isn't allowed to send from the Sender mailbox |
| Graph HTTP 429 or 503 that won't go away | Microsoft is throttling the app. Configure the backup credentials, or wait and try again |

## Known limitations

- **Some values are hard-coded.** The default recipient is in `gui.py`, and the CRM note text, which names that recipient, is in `crm_pipeline.py`.
- **Old numbered copies are deleted.** After the new `<reference>_VAL.pdf` is saved, older `<reference>_VAL2.pdf`-style copies for that reference are deleted.
- **Large PDFs may be rejected.** Attachments are sent inline, and Microsoft only documents inline attachments for files under 3 MB.
- **Windows only.**
