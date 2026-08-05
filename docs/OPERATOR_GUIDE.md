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

Unsafe local-development defaults (never use these on Render):

- Email: `admin@remotectrl.local`
- Password: the local value of `REMOTECTRL_ADMIN_PASSWORD`

## Enroll An Agent

1. Sign in to the dashboard.
2. Click `Create enrollment token`.
3. Run `release\RemoteCtrlAgent-Setup.exe` on the Windows endpoint, then open **RemoteCtrl Agent** from the Start Menu.
4. Enter the gateway URL, for example `http://127.0.0.1:8000` for local dev or `https://<your-render-service>.onrender.com` for public demo.
5. Paste the enrollment token.
6. Click `Enroll`, then `Connect` from the Agent desktop app.

## Run Safe Demo Commands

Recommended demo order:

1. `Processes`
2. `Applications`
3. `Files`
4. `Screen`
5. `Webcam`
6. `Activity Capture`
7. `Power`

Remote commands show a local approval prompt on the agent by default. The Agent user may allow the same action family for the current session only. Stop Live/Stop Session commands also request local approval. Power commands default to dry-run mode.

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
- `release\RemoteCtrlAgent-Setup.exe` and its `.sha256` file exist.
