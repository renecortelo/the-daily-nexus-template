[CmdletBinding()]
param(
    [string]$EpisodeDate = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $env:LOCALAPPDATA "AudioDigest"
$VenvPython = Join-Path $RuntimeDir "venv\Scripts\python.exe"
$NodeBin = Join-Path $RuntimeDir "node-tools\node_modules\.bin"
$LogDir = Join-Path $RuntimeDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("run-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "AudioDigest virtual environment is missing. Run scripts\setup-windows.ps1."
}

$env:PATH = "$NodeBin;$env:PATH"
Remove-Item Env:\OPENAI_API_KEY -ErrorAction Ignore
Remove-Item Env:\CODEX_API_KEY -ErrorAction Ignore
Remove-Item Env:\GEMINI_API_KEY -ErrorAction Ignore
Remove-Item Env:\GOOGLE_API_KEY -ErrorAction Ignore
Remove-Item Env:\GOOGLE_APPLICATION_CREDENTIALS -ErrorAction Ignore
Remove-Item Env:\GOOGLE_CLOUD_PROJECT -ErrorAction Ignore
Remove-Item Env:\GOOGLE_CLOUD_LOCATION -ErrorAction Ignore

$Arguments = @("-m", "audiodigest", "--config", "config.toml", "run")
if ($EpisodeDate) {
    $Arguments += @("--date", $EpisodeDate)
}

Push-Location $ProjectDir
try {
    & $VenvPython @Arguments *>&1 | Tee-Object -FilePath $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "AudioDigest exited with code $LASTEXITCODE. See $LogFile"
    }
} finally {
    Pop-Location
}
