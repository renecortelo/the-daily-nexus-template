[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z][a-z0-9-]{4,28}[a-z0-9]$')]
    [string]$ProjectId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.workers\.dev/?$')]
    [string]$CloudClockUrl
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$NodeModules = Join-Path $env:LOCALAPPDATA 'AudioDigest\node-tools\node_modules'
$FirebaseConfigPath = Join-Path $env:USERPROFILE '.config\configstore\firebase-tools.json'
$ReleaseRoot = Join-Path $ProjectRoot 'tmp\ui-release'
$RemovalManifest = Join-Path $ProjectRoot 'tmp\ui-release-removals.json'

if (-not (Test-Path -LiteralPath $FirebaseConfigPath -PathType Leaf)) {
    throw 'Firebase CLI authorization is unavailable. Run firebase login first.'
}
if (-not (Test-Path -LiteralPath $NodeModules -PathType Container)) {
    throw 'The pinned local Firebase runtime is unavailable. Run the project setup first.'
}

try {
    $FirebaseConfig = Get-Content -LiteralPath $FirebaseConfigPath -Raw | ConvertFrom-Json
    $FirebaseToken = [string]$FirebaseConfig.tokens.refresh_token
} catch {
    throw 'Firebase CLI authorization could not be read securely.'
}
if ($FirebaseToken.Length -lt 20) {
    throw 'Firebase CLI authorization is unavailable. Run firebase login first.'
}

$PreviousFirebaseToken = [Environment]::GetEnvironmentVariable('FIREBASE_TOKEN', 'Process')
$PreviousClockUrl = [Environment]::GetEnvironmentVariable('TDN_CLOUD_CLOCK_URL', 'Process')
$PreviousNodePath = [Environment]::GetEnvironmentVariable('NODE_PATH', 'Process')

try {
    $env:FIREBASE_TOKEN = $FirebaseToken
    $env:TDN_CLOUD_CLOCK_URL = $CloudClockUrl.TrimEnd('/')
    $env:NODE_PATH = $NodeModules
    New-Item -ItemType Directory -Path (Split-Path -Parent $RemovalManifest) -Force | Out-Null
    [System.IO.File]::WriteAllText($RemovalManifest, '[]', [System.Text.UTF8Encoding]::new($false))

    Push-Location $ProjectRoot
    try {
        node .\scripts\prepare-web-console-release.cjs --output .\tmp\ui-release
        if ($LASTEXITCODE -ne 0) { throw 'Private web release staging failed.' }
        node .\scripts\firebase-clone-deploy.cjs `
            --project $ProjectId `
            --public $ReleaseRoot `
            --remove-manifest $RemovalManifest
        if ($LASTEXITCODE -ne 0) { throw 'Private web release deployment failed.' }
    } finally {
        Pop-Location
    }
} finally {
    Remove-Item -LiteralPath $RemovalManifest -Force -ErrorAction SilentlyContinue
    if ($null -eq $PreviousFirebaseToken) {
        Remove-Item Env:FIREBASE_TOKEN -ErrorAction SilentlyContinue
    } else {
        [Environment]::SetEnvironmentVariable('FIREBASE_TOKEN', $PreviousFirebaseToken, 'Process')
    }
    if ($null -eq $PreviousClockUrl) {
        Remove-Item Env:TDN_CLOUD_CLOCK_URL -ErrorAction SilentlyContinue
    } else {
        [Environment]::SetEnvironmentVariable('TDN_CLOUD_CLOCK_URL', $PreviousClockUrl, 'Process')
    }
    if ($null -eq $PreviousNodePath) {
        Remove-Item Env:NODE_PATH -ErrorAction SilentlyContinue
    } else {
        [Environment]::SetEnvironmentVariable('NODE_PATH', $PreviousNodePath, 'Process')
    }
}

Write-Host 'Private web console deployed. The cloud clock endpoint was not written to source control.'
