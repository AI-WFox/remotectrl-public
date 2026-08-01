# RemoteCtrl Agent Desktop

## Purpose

`RemoteCtrl Agent` is the Windows endpoint application for the consent-first RemoteCtrl demo. The dashboard can request actions, but the Windows user sees and decides every protected request locally.

## Architecture

```text
Tauri 2 desktop shell (React/Vite + Tailwind + shadcn/ui)
                  |
            JSON Lines over stdin/stdout
                  |
Python Agent Core sidecar (WebSocket, handlers, consent, streams)
                  |
              RemoteCtrl gateway
```

The desktop shell does not expose a local HTTP service. The sidecar communicates only through its parent Tauri process.

## Build

Prerequisites: Node 24, Rust stable MSVC, Python environment at `agent/.venv`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package_agent_desktop.ps1
```

The command first packages the Python core with PyInstaller, including OpenCV, NumPy, Pillow, psutil and pynput. It then builds the Tauri NSIS installer and embeds a WebView2 bootstrapper.

## Output

- Installer: `agent-desktop\src-tauri\target\release\bundle\nsis\RemoteCtrl Agent_0.2.0_x64-setup.exe`
- Version: `0.2.0`
- Release checksum: `release/RemoteCtrlAgent-Setup.exe.sha256` (regenerated for every build).
- Target: Windows 10/11 x64

The installer is unsigned. Windows SmartScreen may show a warning on a new machine; use only the release artifact produced by this repository.

## Local Smoke Test

```powershell
agent\.venv\Scripts\python.exe scripts\ui_smoke_agent.py
```

The smoke test launches the built Tauri executable with an isolated `APPDATA` directory, verifies its four console pages, then exits.