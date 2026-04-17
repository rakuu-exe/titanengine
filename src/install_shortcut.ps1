$ErrorActionPreference = "Stop"

$AppName = "Titan Engine"
$SourceDir = Join-Path $PSScriptRoot "..\TitanEngine"
$SourceExe = Join-Path $SourceDir "TitanEngine.exe"
$InstallRoot = Join-Path $env:LOCALAPPDATA "TitanEngineApp"
$InstallDir = Join-Path $InstallRoot ("TitanEngine_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$InstallExe = Join-Path $InstallDir "TitanEngine.exe"
$ShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"

if (-not (Test-Path $SourceExe)) {
    throw "Build the app first by running .\build_exe.ps1. Expected file: $SourceExe"
}

Get-Process -Name "TitanEngine" -ErrorAction SilentlyContinue | Stop-Process -Force
if (Test-Path $InstallRoot) {
    attrib -h $InstallRoot 2>$null
}
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force

# Keep the application files out of the student's way while the Desktop shortcut stays visible.
attrib +h $InstallRoot

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $InstallExe
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.Description = "Open Titan Engine"
$Shortcut.IconLocation = "$InstallExe,0"
$Shortcut.Save()

Write-Host "Installed Titan Engine to $InstallDir"
Write-Host "Created Desktop shortcut: $ShortcutPath"
