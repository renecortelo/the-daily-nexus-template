[CmdletBinding()]
param(
    [switch]$InstallFfmpeg
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
if (-not $env:LOCALAPPDATA) {
    throw "LOCALAPPDATA is unavailable. AudioDigest requires a normal Windows user profile."
}
$RuntimeRoot = Join-Path $env:LOCALAPPDATA "AudioDigest"
$VenvDir = Join-Path $RuntimeRoot "venv"
$NodeToolsDir = Join-Path $RuntimeRoot "node-tools"
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeRoot "secrets") | Out-Null
$Python = $null
$PythonPrefix = @()

function Test-CompatiblePython {
    param(
        [string]$Command,
        [string[]]$PrefixArguments = @()
    )
    try {
        & $Command @PrefixArguments -c `
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" `
            *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($Candidate in @("-3.12", "-3.11", "-3")) {
        if (Test-CompatiblePython -Command "py" -PrefixArguments @($Candidate)) {
            $Python = "py"
            $PythonPrefix = @($Candidate)
            break
        }
    }
}
if (-not $Python -and (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Test-CompatiblePython -Command "python") {
        $Python = "python"
    }
}
if (-not $Python) {
    throw @"
Python 3.11 or newer is not installed. The Microsoft Store app alias is not a Python runtime.
Install Python 3.12, close and reopen PowerShell, then rerun this script.
With Windows Package Manager:
  winget install --exact --id Python.Python.3.12
"@
}

if (-not (Get-Command node -ErrorAction SilentlyContinue) -or
    -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw @"
Node.js LTS and npm are required for the local Firebase tool.
Install Node.js LTS, close and reopen PowerShell, then rerun this script.
With Windows Package Manager:
  winget install --exact --id OpenJS.NodeJS.LTS
"@
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if (-not $UvCommand) {
    throw @"
The free uv package installer is required because pip TLS downloads are unreliable on this
Windows setup. Install uv, close and reopen PowerShell, then rerun this script:
  winget install --exact --id astral-sh.uv
"@
}

& $Python @PythonPrefix -m venv $VenvDir
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
    throw "Python could not create the project environment at $VenvDir."
}

Push-Location $ProjectDir
try {
    & $UvCommand.Source pip install `
        --python $VenvPython `
        --link-mode hardlink `
        --editable ".[audio,dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "uv could not install the AudioDigest Python packages. Review the error above."
    }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $NodeToolsDir | Out-Null
$PackageConfig = Get-Content -Raw -LiteralPath (Join-Path $ProjectDir "package.json") |
    ConvertFrom-Json
$FirebaseVersion = $PackageConfig.devDependencies.'firebase-tools'
$FirebasePackage = "firebase-tools@$FirebaseVersion"
npm install `
    --prefix $NodeToolsDir `
    --no-save `
    --no-package-lock `
    $FirebasePackage
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the local Firebase tool. Review the npm error above."
}

$FirebaseCommand = Join-Path $NodeToolsDir "node_modules\.bin\firebase.cmd"
if (-not (Test-Path -LiteralPath $FirebaseCommand)) {
    throw "npm finished without creating the Firebase command. Rerun setup."
}

if ($InstallFfmpeg -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "FFmpeg is missing and winget is unavailable. Install FFmpeg manually."
    }
    winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "FFmpeg installation failed. Review the winget error above."
    }
}

$ConfigPath = Join-Path $ProjectDir "config.toml"
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $ProjectDir "config.example.toml") -Destination $ConfigPath
}

& (Join-Path $PSScriptRoot "create-desktop-shortcut.ps1")

Write-Host ""
Write-Host "Local dependencies are installed."
Write-Host "Runtimes: $RuntimeRoot"
Write-Host "The native 'The Daily Nexus' app is now on your desktop."
Write-Host "No scheduled task or background service was installed."
Write-Host "Next: install Antigravity CLI, then run scripts\authenticate.ps1."
