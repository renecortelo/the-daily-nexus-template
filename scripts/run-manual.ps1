[CmdletBinding()]
param(
    [string]$EpisodeDate = ""
)

& (Join-Path $PSScriptRoot "run-daily.ps1") -EpisodeDate $EpisodeDate
Read-Host "Press Enter to close"
