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

## Architecture

The release path is intentionally narrow:

```text
React Web Dashboard
        |
HTTPS REST + WebSocket
        |
FastAPI Gateway + SQLite
        |
Authenticated outbound Agent WebSocket
        |
Tauri 2 Desktop + JSON Lines bridge
        |
Packaged Python Agent Core
        |
Windows APIs and WebView2
```

The Gateway owns authentication, command routing, online state and audit records. The Windows Agent opens the outbound connection and remains the local consent boundary; it does not expose an inbound HTTP service.

## Features

- One-time Agent enrollment and authenticated multi-Agent routing.
- Application and process inspection/control with protected-process guardrails.
- Screen screenshot/live viewing and WebView2 webcam live viewing.
- Webcam snapshots copied from the latest approved live frame in the dashboard.
- Allowed-folder browsing and browser file downloads.
- Visible Activity Capture sessions with realtime events and export.
- CPU, uptime, battery and guarded power actions.
- Local approval decisions and an operator audit trail.
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

#### Download for instructors and testers

The recommended way to run the Windows Agent is the prebuilt installer from
[GitHub Releases](https://github.com/AI-WFox/remotectrl-public/releases/latest):

1. Open the latest release and expand **Assets**.
2. Download `RemoteCtrlAgent-Setup.exe`.
3. Optionally download `RemoteCtrlAgent-Setup.exe.sha256` and verify the installer with:

```powershell
(Get-FileHash -Algorithm SHA256 .\RemoteCtrlAgent-Setup.exe).Hash.ToLower()
```

4. Run the installer, then open **RemoteCtrl Agent** from the Desktop shortcut or Start Menu.
5. Enter the public Gateway URL and a fresh enrollment token created in the Web dashboard.

The coursework installer is currently unsigned. Windows SmartScreen may display a warning; verify the SHA-256 file before selecting **More info** and **Run anyway**. The installer already contains the Tauri desktop app and packaged Python Agent Core, so Python, Node.js and Rust are not required on the tester machine.

To review source code instead, use **Code -> Download ZIP** or clone the repository and open the folder in Visual Studio Code. The source ZIP is not the runnable installer.

#### Build from source

The distributable Agent is a Tauri desktop app with a bundled Python core. Build the NSIS installer with:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\package_agent_desktop.ps1
```

Local artifacts are created in `release/`. Tagged builds are published by
`.github/workflows/release-agent.yml` with these Release assets:

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
## Public Deployment

`Dockerfile` builds the Web dashboard and serves it from the FastAPI service. `render.yaml` defines the public Render demo service. Production deployments must provide strong values for `REMOTECTRL_SECRET_KEY`, `REMOTECTRL_ADMIN_PASSWORD`, `REMOTECTRL_ADMIN_EMAIL`, `REMOTECTRL_CORS_ORIGINS`, and `REMOTECTRL_ENV`; credentials are not stored in this repository.

See `docs/RENDER_PUBLIC_DEMO.md` for deployment and enrollment instructions.

## Coursework Limitations

RemoteCtrl is a consent-first coursework prototype, not a production-secure remote management product. The Render free service can spin down, its SQLite file is temporary, JPEG/base64 streaming is less efficient than WebRTC or binary framing, the Windows installer is unsigned, and webcam behavior depends on Windows drivers, permissions and WebView2. The project does not provide hidden capture, remote mouse/keyboard control, production RBAC/2FA, device attestation or durable PostgreSQL persistence.