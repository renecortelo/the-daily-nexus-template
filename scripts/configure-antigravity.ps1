[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $env:LOCALAPPDATA "AudioDigest"
$AgyWorkspace = Join-Path $RuntimeDir "antigravity-workspace"
$AgyAgentSource = Join-Path $ProjectDir "config\antigravity-agent.md"
$AgyAgentDir = Join-Path $AgyWorkspace ".agents\agents\audio-digest"
$AgySettingsDir = Join-Path $env:USERPROFILE ".gemini\antigravity-cli"
$AgySettingsFile = Join-Path $AgySettingsDir "settings.json"

New-Item -ItemType Directory -Path $AgySettingsDir -Force | Out-Null
New-Item -ItemType Directory -Path $AgyAgentDir -Force | Out-Null
Copy-Item -LiteralPath $AgyAgentSource `
    -Destination (Join-Path $AgyAgentDir "agent.md") -Force

$Settings = [pscustomobject]@{}
if (Test-Path -LiteralPath $AgySettingsFile) {
    try {
        $Settings = Get-Content -LiteralPath $AgySettingsFile -Raw |
            ConvertFrom-Json
    } catch {
        throw "Antigravity settings.json is not valid JSON: $AgySettingsFile"
    }
}
$Settings | Add-Member -NotePropertyName "useG1Credits" -NotePropertyValue $false -Force
$Settings | Add-Member -NotePropertyName "enableTelemetry" -NotePropertyValue $false -Force

$Trusted = @()
if ($Settings.PSObject.Properties.Name -contains "trustedWorkspaces") {
    $Trusted = @($Settings.trustedWorkspaces)
}
if ($Trusted -notcontains $AgyWorkspace) {
    $Trusted += $AgyWorkspace
}
$Settings | Add-Member -NotePropertyName "trustedWorkspaces" `
    -NotePropertyValue $Trusted -Force

$Permissions = [pscustomobject]@{}
if (
    $Settings.PSObject.Properties.Name -contains "permissions" -and
    $null -ne $Settings.permissions
) {
    $Permissions = $Settings.permissions
}
$AllowedPermissions = @()
if ($Permissions.PSObject.Properties.Name -contains "allow") {
    $AllowedPermissions = @($Permissions.allow)
}
$NormalizedWorkspace = $AgyWorkspace.Replace("\", "/")
$ReadPermission = "read_file($NormalizedWorkspace)"
if ($AllowedPermissions -notcontains $ReadPermission) {
    $AllowedPermissions += $ReadPermission
}
$Permissions | Add-Member -NotePropertyName "allow" `
    -NotePropertyValue $AllowedPermissions -Force
$Settings | Add-Member -NotePropertyName "permissions" `
    -NotePropertyValue $Permissions -Force

$SettingsJson = $Settings | ConvertTo-Json -Depth 20
$Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($AgySettingsFile, $SettingsJson, $Utf8WithoutBom)

$Verified = Get-Content -LiteralPath $AgySettingsFile -Raw | ConvertFrom-Json
if ($Verified.useG1Credits -ne $false -or $Verified.enableTelemetry -ne $false) {
    throw "Could not enforce Antigravity privacy and cost settings."
}

Write-Host "Antigravity safety settings enforced:"
Write-Host "  useG1Credits=false"
Write-Host "  enableTelemetry=false"
Write-Host "  settings=$AgySettingsFile"
Write-Host "  workspace=$AgyWorkspace"
Write-Host "  permission=$ReadPermission"
