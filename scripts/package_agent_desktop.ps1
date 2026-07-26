$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Node = Join-Path $Root "tools\node-v24.16.0-win-x64"
$CargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
$env:PATH = "$Node;$CargoBin;$env:PATH"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\package_agent_core.ps1")
if ($LASTEXITCODE -ne 0) { throw "Agent core packaging failed with exit code $LASTEXITCODE" }
Push-Location (Join-Path $Root "agent-desktop")
try { & (Join-Path $Node "npm.cmd") run tauri build } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS packaging failed with exit code $LASTEXITCODE" }

$InstallerSource = Get-ChildItem -Path (Join-Path $Root "agent-desktop\src-tauri\target\release\bundle\nsis") -Filter "*-setup.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $InstallerSource) { throw "Tauri completed but no NSIS installer was found." }

$ReleaseDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$ReleaseInstaller = Join-Path $ReleaseDir "RemoteCtrlAgent-Setup.exe"
Copy-Item -LiteralPath $InstallerSource.FullName -Destination $ReleaseInstaller -Force

$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReleaseInstaller).Hash.ToLower()
Set-Content -LiteralPath "$ReleaseInstaller.sha256" -Value "$Hash  RemoteCtrlAgent-Setup.exe" -NoNewline
Write-Host "Installer ready: release\RemoteCtrlAgent-Setup.exe"