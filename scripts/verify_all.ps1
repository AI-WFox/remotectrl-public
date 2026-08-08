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
if ($LASTEXITCODE -ne 0) { throw "Python tests failed with exit code $LASTEXITCODE" }

Write-Host "Running E2E mock-agent flow..."
& $BackendPython (Join-Path $Root "scripts\e2e_mock_agent.py")
if ($LASTEXITCODE -ne 0) { throw "Mock Agent E2E failed with exit code $LASTEXITCODE" }

Write-Host "Running E2E headless real-agent flow..."
& $AgentPython (Join-Path $Root "scripts\e2e_headless_agent.py")
if ($LASTEXITCODE -ne 0) { throw "Headless Agent E2E failed with exit code $LASTEXITCODE" }

Write-Host "Running agent UI smoke test..."
& $AgentPython (Join-Path $Root "scripts\ui_smoke_agent.py")
if ($LASTEXITCODE -ne 0) { throw "Agent UI smoke failed with exit code $LASTEXITCODE" }

Write-Host "Building dashboard..."
$nodeDir = Join-Path $Root "tools\node-v24.16.0-win-x64"
$env:PATH = "$nodeDir;$env:PATH"
Push-Location (Join-Path $Root "web")
try {
    & $Npm run build
    $DashboardBuildExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($DashboardBuildExitCode -ne 0) { throw "Dashboard build failed with exit code $DashboardBuildExitCode" }

Write-Host "Running packaged desktop E2E..."
& $AgentPython -u (Join-Path $Root "scripts\e2e_web_agent_desktop.py") --extended
if ($LASTEXITCODE -ne 0) { throw "Packaged desktop E2E failed with exit code $LASTEXITCODE" }

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
$DesktopBytes = [System.IO.File]::ReadAllBytes($DesktopExe)
$PeOffset = [BitConverter]::ToInt32($DesktopBytes, 0x3C)
$OptionalHeaderOffset = $PeOffset + 24
$WindowsSubsystem = [BitConverter]::ToUInt16($DesktopBytes, $OptionalHeaderOffset + 68)
if ($WindowsSubsystem -ne 2) {
    throw "RemoteCtrl Agent desktop uses PE subsystem $WindowsSubsystem instead of the Windows GUI subsystem."
}
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash.ToLower()
$StoredHash = ([System.IO.File]::ReadAllText($Checksum)).Split(' ')[0].Trim().ToLower()
if ($Hash -ne $StoredHash) { throw "Installer checksum does not match $Checksum" }

Write-Host "RemoteCtrl verification completed."
