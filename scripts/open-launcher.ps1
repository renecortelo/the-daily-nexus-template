[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $env:LOCALAPPDATA "AudioDigest\venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "AudioDigest is not installed. Run scripts\setup-windows.ps1 first."
}

Start-Process `
    -FilePath $Pythonw `
    -ArgumentList @("-m", "audiodigest.launcher", "--project-dir", "`"$ProjectDir`"") `
    -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden
