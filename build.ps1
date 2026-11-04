<#
.SYNOPSIS
    Builds ValidatedTitlesSender.exe with PyInstaller.

.DESCRIPTION
    Bundles Tesseract and Poppler into the build as tesseract\ and poppler\,
    the folders ocr_utils.py looks in at run time. Paths are resolved from
    this script's folder, so it can be run from anywhere.

    By default the project's .env is bundled too. That copies the client
    secret into the output folder as plain text. Use -ExcludeEnv to leave it
    out and put a .env next to the .exe on each PC instead.

.EXAMPLE
    .\build.ps1

.EXAMPLE
    .\build.ps1 -DistPath C:\Builds -ExcludeEnv
#>
[CmdletBinding()]
param(
    # Tesseract's install folder (holds tesseract.exe and tessdata\)
    [string]$TesseractDir = "C:\Program Files\Tesseract-OCR",

    # Poppler's bin folder (holds pdftoppm.exe and pdfinfo.exe)
    [string]$PopplerBin = "C:\Program Files\poppler\Library\bin",

    # Where the app is built: <DistPath>\<Name>\<Name>.exe
    [string]$DistPath = "D:\sendEmail",

    # PyInstaller's working folder (also gets the generated .spec file)
    [string]$WorkPath = "C:\Temp\VShipBuild\build",

    [string]$Name = "ValidatedTitlesSender",

    # Don't bundle .env; put one next to the .exe instead
    [switch]$ExcludeEnv
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = $PSScriptRoot

$required = @(
    @{ Path = (Join-Path $TesseractDir "tesseract.exe"); Hint = "Install Tesseract or pass -TesseractDir." },
    @{ Path = (Join-Path $PopplerBin "pdftoppm.exe"); Hint = "Install Poppler or pass -PopplerBin." }
)
foreach ($item in $required) {
    if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf)) {
        throw "Not found: $($item.Path). $($item.Hint)"
    }
}

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    throw "pyinstaller isn't on PATH. Activate the project's virtual environment and run: pip install pyinstaller"
}

$pyinstallerArgs = @(
    "--noconfirm", "--clean", "--onedir", "--windowed",
    "--name", $Name,
    "--paths", $root,
    "--add-data", "$TesseractDir;tesseract",
    "--add-data", "$PopplerBin;poppler",
    "--workpath", $WorkPath,
    "--distpath", $DistPath,
    "--specpath", $WorkPath
)

$envFile = Join-Path $root ".env"
if ($ExcludeEnv) {
    Write-Host "Not bundling .env. Put a .env next to $Name.exe on each PC."
}
elseif (Test-Path -LiteralPath $envFile -PathType Leaf) {
    Write-Warning ".env, including the client secret, will be copied into the build as plain text. Use -ExcludeEnv to leave it out."
    $pyinstallerArgs += @("--add-data", "$envFile;.")
}
else {
    throw "There's no .env in $root. Create one, or build with -ExcludeEnv and put a .env next to the .exe."
}

$pyinstallerArgs += (Join-Path $root "main.py")

& pyinstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed (exit code $LASTEXITCODE)."
}

Write-Host "Built $(Join-Path (Join-Path $DistPath $Name) "$Name.exe")"
