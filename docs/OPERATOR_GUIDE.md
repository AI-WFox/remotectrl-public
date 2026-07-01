# RemoteCtrl Operator Guide

## Start The System

```powershell
.\scripts\start_dev.ps1
```

If PowerShell script execution is restricted, use:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\start_dev.ps1
```

Open:

- Dashboard: `http://127.0.0.1:5173`
- Backend health: `http://127.0.0.1:8000/api/health`

For a public demo where agents can connect from any network, deploy the Docker service on Render and use the Render URL instead. See `docs/RENDER_PUBLIC_DEMO.md`.

Default credentials:

- Email: `admin@remotectrl.local`
- Password: `admin12345`

## Enroll An Agent

1. Sign in to the dashboard.
2. Click `Create enrollment token`.
3. Run `agent\dist\RemoteCtrlAgent.exe` on the Windows endpoint.
4. Enter the gateway URL, for example `http://127.0.0.1:8000` for local dev or `https://<your-render-service>.onrender.com` for public demo.
5. Paste the enrollment token.
6. Click `Enroll`, then `Connect`.

## Run Safe Demo Commands

Recommended demo order:

1. `Processes`
2. `Applications`
3. `Files`
4. `Screen`
5. `Webcam`
6. `Key Capture`
7. `Power`

Remote commands show a local approval prompt on the agent by default. The Agent user may allow the same action family for the current session only. Stop Live/Stop Session commands do not prompt because they reduce access. Power commands default to dry-run mode.

## Verify Before Demo

```powershell
.\scripts\verify_all.ps1
```

Fallback if script execution is restricted:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\verify_all.ps1
```

Expected:

- Python tests pass.
- E2E mock-agent flow passes.
- Dashboard production build passes.
- `agent\dist\RemoteCtrlAgent.exe` exists.
