$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$AgentPython = Join-Path $Root "agent\.venv\Scripts\python.exe"
$BinaryDir = Join-Path $Root "agent-desktop\src-tauri\binaries"
$Target = Join-Path $BinaryDir "remotectrl-agent-core-x86_64-pc-windows-msvc.exe"

if (!(Test-Path $AgentPython)) { throw "Agent venv not found. Install agent requirements first." }
New-Item -ItemType Directory -Path $BinaryDir -Force | Out-Null
& $AgentPython -m PyInstaller `
  --onefile `
  --noconsole `
  --name remotectrl-agent-core `
  --collect-all numpy `
  --collect-all PIL `
  --hidden-import pynput.keyboard._win32 `
  --hidden-import pynput.mouse._win32 `
  --exclude-module PySide6 `
  --exclude-module tkinter `
  (Join-Path $Root "agent\remotectrl_agent\__main__.py") `
  --distpath (Join-Path $Root "agent-desktop\build\core") `
  --workpath (Join-Path $Root "agent-desktop\build\pyinstaller") `
  --specpath (Join-Path $Root "agent-desktop\build")
if ($LASTEXITCODE -ne 0) { throw "Agent core packaging failed with exit code $LASTEXITCODE" }
Copy-Item -LiteralPath (Join-Path $Root "agent-desktop\build\core\remotectrl-agent-core.exe") -Destination $Target -Force
Write-Host "Built $Target"
