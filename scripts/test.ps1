[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $env:LOCALAPPDATA "AudioDigest\venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}
$env:PYTHONPATH = Join-Path $ProjectDir "src"
Push-Location $ProjectDir
try {
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed."
    }
} finally {
    Pop-Location
}
