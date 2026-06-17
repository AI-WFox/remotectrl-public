$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
$AgentPython = Join-Path $Root "agent\.venv\Scripts\python.exe"
$Npm = Join-Path $Root "tools\node-v24.16.0-win-x64\npm.cmd"

if (!(Test-Path $BackendPython)) { throw "Missing backend venv: $BackendPython" }
if (!(Test-Path $AgentPython)) { throw "Missing agent venv: $AgentPython" }
if (!(Test-Path $Npm)) { $Npm = "npm" }

Write-Host "Running Python tests..."
& $BackendPython -m pytest

Write-Host "Running E2E mock-agent flow..."
& $BackendPython (Join-Path $Root "scripts\e2e_mock_agent.py")

Write-Host "Running E2E headless real-agent flow..."
& $AgentPython (Join-Path $Root "scripts\e2e_headless_agent.py")

Write-Host "Running agent UI smoke test..."
& $AgentPython (Join-Path $Root "scripts\ui_smoke_agent.py")

Write-Host "Building dashboard..."
$nodeDir = Join-Path $Root "tools\node-v24.16.0-win-x64"
$env:PATH = "$nodeDir;$env:PATH"
Push-Location (Join-Path $Root "web")
try {
    & $Npm run build
}
finally {
    Pop-Location
}

Write-Host "Checking packaged agent artifact..."
$AgentExe = Join-Path $Root "agent\dist\RemoteCtrlAgent.exe"
if (!(Test-Path $AgentExe)) {
    throw "Missing $AgentExe. Run scripts\package_agent.ps1"
}

Write-Host "RemoteCtrl verification completed."
