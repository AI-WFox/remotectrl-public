# End-to-End Test Lab

`e2e_web_agent_desktop.py` validates the complete consent flow against disposable local state:

```text
Playwright dashboard -> FastAPI gateway -> packaged Tauri Agent -> pywinauto approval dialog -> dashboard result/audit
```

## Default suite

The default run uses isolated temporary folders and validates:

- Browser login and dashboard enrollment-token creation.
- Tauri Agent enrollment through its Settings UI.
- Agent online state in the dashboard.
- Local approval windows and `Allow once` responses.
- Applications list, Processes list, Files roots, Screen screenshot, Webcam diagnostics, and Power telemetry.
- Command result rendering and `approval.response` audit entries.

It never calls a destructive Power action. The test Agent has a temporary allowed folder containing only an E2E fixture file.

## Run

```powershell
$env:TMP = 'D:\Project\MMT\.tmp'
$env:TEMP = 'D:\Project\MMT\.tmp'
agent\.venv\Scripts\python.exe scripts\e2e_web_agent_desktop.py
```

Requirements: the packaged Tauri executable must exist at `agent-desktop/src-tauri/target/release/remotectrl-agent-desktop.exe`, Google Chrome must be installed, and the agent development environment must include `agent/requirements-dev.txt`.

`--extended` additionally exercises Notepad and Activity Capture. Run it only on a disposable test desktop because it opens a real application and activates a visible capture session.