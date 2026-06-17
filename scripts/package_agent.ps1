$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AgentPython = Join-Path $Root "agent\.venv\Scripts\python.exe"

if (!(Test-Path $AgentPython)) {
    throw "Agent venv not found. Run: python -m venv agent\.venv; agent\.venv\Scripts\python.exe -m pip install -r agent\requirements-dev.txt"
}

& $AgentPython -m PyInstaller `
    --onefile `
    --windowed `
    --name RemoteCtrlAgent `
    (Join-Path $Root "agent\remotectrl_agent\__main__.py") `
    --distpath (Join-Path $Root "agent\dist") `
    --workpath (Join-Path $Root "agent\build") `
    --specpath (Join-Path $Root "agent")

Write-Host "Built agent\dist\RemoteCtrlAgent.exe"

