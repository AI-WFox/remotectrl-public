# Demo Script

1. Open the dashboard and show the light/dark toggle.
2. Show the agent list and the connected Windows agent.
3. Open the command drawer and audit timeline.
4. Run `process.list` and explain \(O(n)\) enumeration.
5. Run `app.list` and show visible applications.
6. Trigger `screen.screenshot`; approve locally on the agent.
7. Browse allowed files and download a demo file after approval.
8. Check cameras, start Webcam Live with local approval, then capture a snapshot from the latest live frame.
9. Start a visible Activity Capture session and export the approved activity log.
10. Trigger power command in dry-run mode and show audit log.

Close by showing all actions in the audit timeline to prove consent and traceability.

## Automated E2E Check

Before the live classroom demo, run:

```powershell
backend\.venv\Scripts\python.exe scripts\e2e_mock_agent.py
agent\.venv\Scripts\python.exe scripts\e2e_headless_agent.py
agent\.venv\Scripts\python.exe scripts\ui_smoke_agent.py
```

Expected output:

```text
E2E mock agent flow passed
```
