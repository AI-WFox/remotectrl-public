# Roadmap

## Implemented now

- FastAPI gateway with SQLite schema, auth token, enrollment, commands, audit, WebSocket routing.
- Backend command lifecycle with `pending_approval`, offline failure handling, bootstrap capability metadata, API integration tests.
- React/Vite dashboard shell with premium ops UI, light/dark mode, demo mode, agent/module navigation, command timeline, result card, audit drawer.
- Windows Agent Desktop with Tauri 2, React/Vite, Tailwind, shadcn/ui, bundled Python core, visible approval and activity indicators.
- Packaged NSIS installer at `release/RemoteCtrlAgent-Setup.exe`.

## Verified

- `backend/.venv/Scripts/python.exe -m pytest` passes: backend + agent tests.
- `backend/.venv/Scripts/python.exe scripts/e2e_mock_agent.py` passes: real gateway + mock WebSocket agent + command result.
- `agent/.venv/Scripts/python.exe scripts/e2e_headless_agent.py` passes: real gateway + real headless agent core + real process listing.
- `agent/.venv/Scripts/python.exe scripts/ui_smoke_agent.py` passes: Windows agent app window opens.
- `agent/.venv/Scripts/python.exe -m pytest agent/tests` passes.
- `tools/node-v24.16.0-win-x64/npm.cmd run build` passes for the React/Vite dashboard.
- `scripts/package_agent_desktop.ps1` builds the Tauri NSIS installer and SHA-256 release artifact.

## Next hardening steps

1. Run backend, dashboard, and packaged agent together on LAN for full manual demo QA.
2. Add visual regression/browser screenshots once the in-app browser sandbox issue is resolved.
3. Replace demo HMAC auth with secure cookies or JWT + stronger password hashing.
4. Add PostgreSQL migration path.
5. Add signed agent packaging and update channel.
6. Add browser E2E tests and Windows UI tests using `pywinauto`.
