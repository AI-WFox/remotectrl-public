# RemoteCtrl

Production-like remote-control demo for Computer Networking coursework.

RemoteCtrl is built as a consent-first LAN control platform:

```text
React/Vite Web Dashboard
        |
FastAPI Gateway + WebSocket
        |
Downloadable Windows Agent App
```

The legacy material in `Resource/` is reference-only. The implementation in this repo is new code.

## Safety Model

Remote-control capabilities can be abused when implemented as hidden tooling. RemoteCtrl intentionally avoids stealth behavior:

- Agents are visible desktop applications.
- Remote actions require local approval by default.
- Agent users can allow a specific command/resource scope for the current session only; approvals reset on disconnect/restart/manual reset.
- Screen, webcam, files, applications, processes, power actions, and visible Activity Capture sessions are audited.
- Activity Capture is a visible, locally approved session; it is not a hidden keylogger.
- File access is restricted to configured allowed folders.
- Power commands default to dry-run mode until explicitly enabled.

## Project Layout

- `backend/` - FastAPI API, WebSocket gateway, SQLite persistence.
- `web/` - React/Vite dashboard with premium ops UI.
- `agent/` - Python Agent Core: WebSocket, handlers, consent, streams.
- `agent-desktop/` - Windows Tauri desktop shell, React UI, NSIS installer.
- `docs/` - architecture, demo, and security notes.
- `Resource/` - teacher-provided/reference material.

## One-Command Operations

```powershell
.\scripts\verify_all.ps1
.\scripts\start_dev.ps1
.\scripts\package_agent_desktop.ps1
```

If PowerShell blocks local scripts, run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1
```

See [Operator Guide](docs/OPERATOR_GUIDE.md) for the demo runbook.

## Quick Start

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:REMOTECTRL_ADMIN_PASSWORD = "<choose-a-local-password>"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Unsafe local-development defaults (production startup rejects these values):

- Email: `admin@remotectrl.local`
- Password: the value set in `REMOTECTRL_ADMIN_PASSWORD`

### Web

```powershell
cd web
npm install
npm run dev
```

The dashboard also has a premium demo mode on the sign-in screen, so the UI can be inspected even before a gateway is running.

### Windows Agent Desktop

The distributable Agent is the Tauri desktop app with a bundled Python core. Build the NSIS installer with:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\package_agent_desktop.ps1
```

Tester artifacts are created locally in `release/`. Tagged builds are also published by
`.github/workflows/release-agent.yml` on the repository GitHub Releases page:

```text
RemoteCtrlAgent-Setup.exe
RemoteCtrlAgent-Setup.exe.sha256
```

For desktop development, see `docs/AGENT_DESKTOP.md`.

## Verification

Commands run successfully in this workspace:

```powershell
backend\.venv\Scripts\python.exe -m pytest
backend\.venv\Scripts\python.exe scripts\e2e_mock_agent.py
agent\.venv\Scripts\python.exe scripts\e2e_headless_agent.py
agent\.venv\Scripts\python.exe scripts\ui_smoke_agent.py
agent\.venv\Scripts\python.exe -m pytest agent\tests
tools\node-v24.16.0-win-x64\npm.cmd install
tools\node-v24.16.0-win-x64\npm.cmd run build
powershell.exe -ExecutionPolicy Bypass -File .\scripts\package_agent_desktop.ps1
```

This Codex environment did not have global `npm`, so a portable official Node.js LTS (`v24.16.0`) was used from `tools/` for verification. `tools/` is ignored and can be recreated.

The E2E scripts cover both a mock WebSocket agent and the real headless agent core. The headless agent check uses `AgentClient` and `CommandHandlers` to return a real `process.list` result from the current machine. The UI smoke test opens the Windows agent app and verifies the `RemoteCtrl Agent` window appears.

## Demo Flow

1. Start backend.
2. Open dashboard.
3. Create or copy an enrollment token.
4. Start agent app and enroll it with backend URL + token.
5. Run commands with local approval: process list, applications list, file browse/download, screen/webcam live, Activity Capture, and audit review.
