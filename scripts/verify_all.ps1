$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
$AgentPython = Join-Path $Root "agent\.venv\Scripts\python.exe"
$Npm = Join-Path $Root "tools\node-v24.16.0-win-x64\npm.cmd"

if (!(Test-Path $BackendPython)) { throw "Missing backend venv: $BackendPython" }
if (!(Test-Path $AgentPython)) { throw "Missing agent venv: $AgentPython" }
if (!(Test-Path $Npm)) { $Npm = "npm" }

Write-Host "Running Python tests..."
& $BackendPython -m pytest backend\tests agent\tests

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

Write-Host "Running packaged desktop E2E..."
& $AgentPython -u (Join-Path $Root "scripts\e2e_web_agent_desktop.py") --extended

Write-Host "Checking Tauri/NSIS release artifacts..."
$DesktopExe = Join-Path $Root "agent-desktop\src-tauri\target\release\remotectrl-agent-desktop.exe"
$Installer = Join-Path $Root "release\RemoteCtrlAgent-Setup.exe"
$Checksum = "$Installer.sha256"
foreach ($Artifact in @($DesktopExe, $Installer, $Checksum)) {
    if (!(Test-Path $Artifact)) { throw "Missing $Artifact. Run scripts\package_agent_desktop.ps1" }
}
foreach ($Executable in @($DesktopExe, $Installer)) {
    $Bytes = [System.IO.File]::ReadAllBytes($Executable)
    if ($Bytes.Length -lt 2 -or $Bytes[0] -ne 0x4D -or $Bytes[1] -ne 0x5A) {
        throw "Invalid Windows executable header: $Executable"
    }
}
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash.ToLower()
$StoredHash = ([System.IO.File]::ReadAllText($Checksum)).Split(' ')[0].Trim().ToLower()
if ($Hash -ne $StoredHash) { throw "Installer checksum does not match $Checksum" }

Write-Host "RemoteCtrl verification completed."
