# Render Public Demo Guide

This guide runs RemoteCtrl as one public Render web service:

- React dashboard
- FastAPI REST API
- Dashboard WebSocket
- Agent WebSocket

## 1. Push The Repo To GitHub

Render deploys from a GitHub repository. Make sure these files are committed:

- `Dockerfile`
- `.dockerignore`
- `render.yaml`
- `backend/`
- `web/`

## 2. Create The Render Service

1. Open Render.
2. Create a new Blueprint from the GitHub repo, or create a Web Service manually.
3. Use Docker runtime.
4. Use the free plan.
5. Set the health check path:

```text
/api/health
```

## 3. Configure Environment Variables

Set these in Render:

```text
REMOTECTRL_ENV=production
REMOTECTRL_SECRET_KEY=<generated strong secret>
REMOTECTRL_ADMIN_EMAIL=<admin email>
REMOTECTRL_ADMIN_PASSWORD=<strong password>
REMOTECTRL_CORS_ORIGINS=https://<your-render-service>.onrender.com
```

If the final Render URL is different from `https://remotectrl-public-demo.onrender.com`, update `REMOTECTRL_CORS_ORIGINS` to match the real URL.

## 4. Verify The Public Gateway

Open:

```text
https://<your-render-service>.onrender.com/api/health
```

Expected:

```json
{"status":"ok","service":"remotectrl-gateway"}
```

Then open:

```text
https://<your-render-service>.onrender.com
```

Sign in with the Render admin email/password.

## 5. Connect An Agent From Anywhere

1. In the public dashboard, click `Create enrollment token`.
2. Send the Windows agent app to the endpoint user.
3. In the agent app, enter:

```text
Gateway URL: https://<your-render-service>.onrender.com
Enrollment token: <token from dashboard>
```

4. Click `Enroll`, then `Connect`.

The Agent opens `wss://<your-render-service>.onrender.com/ws/agent` and sends its
Agent credential in the first authenticated WebSocket message. The credential is
not placed in the URL. The dashboard obtains a short-lived, one-time ticket from
`POST /api/auth/ws-ticket` before opening `/ws/dashboard`.

## Demo Limits On Render Free

- The service can spin down after idle time.
- SQLite data can reset after redeploy/restart.
- If the service resets, create a new enrollment token and enroll the agent again.
- If live stream is laggy, reduce stream FPS from `10` to `5`.
