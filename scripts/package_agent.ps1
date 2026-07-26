$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\package_agent_desktop.ps1")
if ($LASTEXITCODE -ne 0) { throw "RemoteCtrl Agent desktop packaging failed with exit code $LASTEXITCODE" }