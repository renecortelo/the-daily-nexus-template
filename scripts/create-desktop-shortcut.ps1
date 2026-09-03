[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $env:LOCALAPPDATA "AudioDigest\venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "AudioDigest is not installed. Run scripts\setup-windows.ps1 first."
}

$IconPath = Join-Path $ProjectDir "assets\tdn-retrofuture.ico"
if (-not (Test-Path -LiteralPath $IconPath)) {
    throw "The Daily Nexus application icon is missing: $IconPath"
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$ApplicationPath = Join-Path $Desktop "The Daily Nexus.exe"
$RuntimeLauncherDir = Join-Path $env:LOCALAPPDATA "AudioDigest\launcher"
$RuntimeApplication = Join-Path $RuntimeLauncherDir "The Daily Nexus.exe"
$RuntimeSource = Join-Path $RuntimeLauncherDir "TheDailyNexusLauncher.cs"
New-Item -ItemType Directory -Path $RuntimeLauncherDir -Force | Out-Null

$EscapedProjectDir = $ProjectDir.Replace('"', '""')
$Source = @"
using System;
using System.Diagnostics;
using System.IO;

internal static class TheDailyNexusLauncher
{
    [STAThread]
    private static int Main()
    {
        string projectDir = @"$EscapedProjectDir";
        string pythonw = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "AudioDigest", "venv", "Scripts", "pythonw.exe"
        );
        if (!File.Exists(pythonw) || !Directory.Exists(projectDir))
        {
            return 2;
        }
        ProcessStartInfo start = new ProcessStartInfo();
        start.FileName = pythonw;
        start.Arguments = "-m audiodigest.launcher --project-dir \"" + projectDir + "\"";
        start.WorkingDirectory = projectDir;
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        Process.Start(start);
        return 0;
    }
}
"@

if (Test-Path -LiteralPath $RuntimeApplication) {
    Remove-Item -LiteralPath $RuntimeApplication -Force
}
$Source | Set-Content -LiteralPath $RuntimeSource -Encoding utf8
$CompilerCandidates = @(
    (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
)
$Compiler = $CompilerCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $Compiler) {
    throw "The built-in Windows C# compiler is unavailable."
}
& $Compiler `
    /nologo `
    /target:winexe `
    "/win32icon:$IconPath" `
    "/out:$RuntimeApplication" `
    $RuntimeSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $RuntimeApplication)) {
    throw "Windows could not build The Daily Nexus desktop app."
}
Copy-Item -LiteralPath $RuntimeApplication -Destination $ApplicationPath -Force

foreach ($OldPath in @(
    (Join-Path $Desktop "The Daily Nexus.lnk"),
    (Join-Path $Desktop "Daily Signal.lnk")
)) {
    if (Test-Path -LiteralPath $OldPath) {
        Remove-Item -LiteralPath $OldPath -Force
    }
}

Write-Host "Created native desktop app: $ApplicationPath"
