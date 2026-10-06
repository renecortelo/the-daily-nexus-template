[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
if (-not $env:LOCALAPPDATA) { throw 'A normal Windows user profile is required.' }
$Version = '1.3.0'
$ExpectedHash = '9b099659cb24214da23f3f124dc91c69e22cec2ab0254d9bf82b10c6ad05566d'
$DownloadUrl = "https://github.com/google-antigravity/antigravity-cli/releases/download/$Version/agy_cli_windows_x64.zip"
$TemporaryRoot = Join-Path $env:TEMP ('tdn-agy-' + [guid]::NewGuid().ToString('N'))
$InstallRoot = Join-Path $env:LOCALAPPDATA 'agy\bin'
New-Item -ItemType Directory -Path $TemporaryRoot | Out-Null
$Archive = Join-Path $TemporaryRoot 'agy.zip'
Invoke-WebRequest -Uri $DownloadUrl -OutFile $Archive
if ((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
    throw 'Antigravity archive failed SHA-256 verification. Nothing was installed.'
}
Expand-Archive -LiteralPath $Archive -DestinationPath (Join-Path $TemporaryRoot 'unpacked')
$Binary = Join-Path $TemporaryRoot 'unpacked\antigravity.exe'
if (-not (Test-Path -LiteralPath $Binary)) { throw 'Verified archive has no expected executable.' }
$ActualVersion = & $Binary --version
if ($LASTEXITCODE -ne 0 -or $ActualVersion.Trim() -ne $Version) {
    throw 'Antigravity executable version mismatch. Nothing was installed.'
}
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$Destination = Join-Path $InstallRoot 'agy.exe'
if (Test-Path -LiteralPath $Destination) {
    Copy-Item -LiteralPath $Destination -Destination (Join-Path $TemporaryRoot 'previous-agy.exe')
}
Copy-Item -LiteralPath $Binary -Destination $Destination
Write-Host "Antigravity $Version installed. Authentication and safety settings were not changed."
Write-Host "Verified download and recoverable previous executable: $TemporaryRoot"
