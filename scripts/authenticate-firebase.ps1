[CmdletBinding()]
param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$FirebaseCommand = Join-Path $env:LOCALAPPDATA "AudioDigest\node-tools\node_modules\.bin\firebase.cmd"
$NodeCommand = Join-Path $env:ProgramFiles "nodejs\node.exe"
$PrivacyScript = Join-Path $PSScriptRoot "configure-firebase-privacy.js"

if (-not (Test-Path -LiteralPath $FirebaseCommand)) {
    throw "Firebase CLI is not installed. Run scripts\setup-windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $NodeCommand)) {
    throw "Node.js is not installed. Run scripts\setup-windows.ps1 first."
}

Push-Location $ProjectDir
try {
    Write-Host "The Daily Nexus // Firebase Spark sign-in"
    Write-Host ""
    Write-Host "Use the Google account that owns the dedicated Firebase project."
    Write-Host "This signs in the local Firebase command only; it does not enable publishing."
    Write-Host "If Firebase asks about Gemini features or usage reporting, answer No."
    Write-Host ""
    & $NodeCommand $PrivacyScript
    if ($LASTEXITCODE -ne 0) {
        throw "Could not disable Firebase CLI Gemini features and usage reporting."
    }
    & $FirebaseCommand login
    if ($LASTEXITCODE -ne 0) {
        throw "Firebase CLI authentication failed."
    }
    & $NodeCommand $PrivacyScript
    if ($LASTEXITCODE -ne 0) {
        throw "Could not recheck Firebase CLI privacy preferences."
    }
    Write-Host ""
    Write-Host "Firebase authentication succeeded with optional CLI telemetry and Gemini off."
    Write-Host "Return to The Daily Nexus, verify Spark with no billing, then enable publishing."
    if (-not $NoPause) {
        Read-Host "Press Enter to close"
    }
} finally {
    Pop-Location
}
