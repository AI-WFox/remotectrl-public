param(
    [string]$HostAddress = "127.0.0.1",
    [int]$BackendPort = 8000,
    [int]$WebPort = 5173
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
$Npm = Join-Path $Root "tools\node-v24.16.0-win-x64\npm.cmd"

if (!(Test-Path $BackendPython)) {
    throw "Backend venv not found. Run backend setup first: python -m venv backend\.venv; backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt"
}

if (!(Test-Path $Npm)) {
    $Npm = "npm"
}

Write-Host "Starting RemoteCtrl backend on http://$HostAddress`:$BackendPort"
Start-Process -WindowStyle Hidden -FilePath $BackendPython -ArgumentList "-m","uvicorn","app.main:app","--host",$HostAddress,"--port",$BackendPort -WorkingDirectory (Join-Path $Root "backend")

Write-Host "Starting RemoteCtrl dashboard on http://$HostAddress`:$WebPort"
$nodeDir = Join-Path $Root "tools\node-v24.16.0-win-x64"
$env:PATH = "$nodeDir;$env:PATH"
Start-Process -WindowStyle Hidden -FilePath $Npm -ArgumentList "run","dev","--","--host",$HostAddress,"--port",$WebPort -WorkingDirectory (Join-Path $Root "web")

Write-Host "RemoteCtrl dev servers requested."
Write-Host "Dashboard: http://$HostAddress`:$WebPort"
Write-Host "Backend:   http://$HostAddress`:$BackendPort/api/health"

