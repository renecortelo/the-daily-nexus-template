[CmdletBinding()]
param(
    [switch]$IncludeFirebase
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $env:LOCALAPPDATA "AudioDigest"
$VenvPython = Join-Path $RuntimeDir "venv\Scripts\python.exe"
$NodeBin = Join-Path $RuntimeDir "node-tools\node_modules\.bin"
$AgyExe = Join-Path $env:LOCALAPPDATA "agy\bin\agy.exe"
$AgyWorkspace = Join-Path $RuntimeDir "antigravity-workspace"
$NodeInstallDir = Join-Path $env:ProgramFiles "nodejs"
$env:PATH = "$NodeBin;$NodeInstallDir;$env:PATH"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Run scripts\setup-windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $AgyExe)) {
    throw @"
Google replaced Gemini CLI consumer sign-in with Antigravity CLI in June 2026.
Review Google's official Windows installation instructions, install the CLI,
then run this authentication script again:

  https://www.antigravity.google/docs/cli/install/
"@
}

Push-Location $ProjectDir
try {
    & (Join-Path $PSScriptRoot "configure-antigravity.ps1")

    foreach ($Name in @(
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "VERTEX_AI_PROJECT"
    )) {
        Remove-Item "Env:\$Name" -ErrorAction Ignore
    }

    Write-Host "1/2 Sign in to Antigravity with the Google account that owns Google AI Pro."
    Write-Host "Choose Google OAuth. Do not choose a Google Cloud project."
    Write-Host "After onboarding finishes, type /quit (or press Ctrl+D) to return here."
    Push-Location $AgyWorkspace
    try {
        & $AgyExe
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) { throw "Antigravity authentication failed." }
    & (Join-Path $PSScriptRoot "configure-antigravity.ps1")

    if ($IncludeFirebase) {
        Write-Host "Optional: sign in to Firebase CLI with the same Google account."
        & (Join-Path $PSScriptRoot "authenticate-firebase.ps1") -NoPause
    }

    Write-Host "2/2 Authorize read-only Gmail access."
    & $VenvPython -m audiodigest --config config.toml authenticate-gmail
    if ($LASTEXITCODE -ne 0) { throw "Gmail authorization or label verification failed." }
    Write-Host ""
    Write-Host "Authentication is complete."
} finally {
    Pop-Location
}
