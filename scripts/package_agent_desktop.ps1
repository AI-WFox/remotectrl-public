$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BundledNode = Join-Path $Root "tools\node-v24.16.0-win-x64"
$CargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path (Join-Path $BundledNode "npm.cmd")) {
    $Npm = Join-Path $BundledNode "npm.cmd"
    $env:PATH = "$BundledNode;$CargoBin;$env:PATH"
}
else {
    $NpmCommand = Get-Command npm.cmd -ErrorAction Stop
    $Npm = $NpmCommand.Source
    $env:PATH = "$CargoBin;$env:PATH"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\package_agent_core.ps1")
if ($LASTEXITCODE -ne 0) { throw "Agent core packaging failed with exit code $LASTEXITCODE" }
Push-Location (Join-Path $Root "agent-desktop")
try { & $Npm run tauri build } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "Tauri NSIS packaging failed with exit code $LASTEXITCODE" }

$InstallerSource = Get-ChildItem -Path (Join-Path $Root "agent-desktop\src-tauri\target\release\bundle\nsis") -Filter "*-setup.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $InstallerSource) { throw "Tauri completed but no NSIS installer was found." }

$ReleaseDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$ReleaseInstaller = Join-Path $ReleaseDir "RemoteCtrlAgent-Setup.exe"
$SourceBytes = [System.IO.File]::ReadAllBytes($InstallerSource.FullName)
if ($SourceBytes.Length -lt 2 -or $SourceBytes[0] -ne 0x4D -or $SourceBytes[1] -ne 0x5A) {
    throw "Tauri produced an invalid installer. Expected a Windows MZ executable header."
}
$SourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerSource.FullName).Hash.ToLower()
Copy-Item -LiteralPath $InstallerSource.FullName -Destination $ReleaseInstaller -Force
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReleaseInstaller).Hash.ToLower()
if ($Hash -ne $SourceHash) {
    throw "Installer copy verification failed. The release file does not match the NSIS output."
}
$ChecksumPath = "$ReleaseInstaller.sha256"
[System.IO.File]::WriteAllText($ChecksumPath, "$Hash  RemoteCtrlAgent-Setup.exe", [System.Text.UTF8Encoding]::new($false))
$StoredHash = ([System.IO.File]::ReadAllText($ChecksumPath)).Split(' ')[0].Trim().ToLower()
if ($StoredHash -ne $Hash) {
    throw "Checksum file verification failed."
}
Write-Host "Installer ready: release\RemoteCtrlAgent-Setup.exe"