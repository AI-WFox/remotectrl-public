# RemoteCtrl Architecture

## Runtime Topology

```text
Operator Browser
   |
   | HTTPS / WebSocket
   v
FastAPI Gateway
   |-- REST: auth, agents, commands, audit
   |-- WS /ws/dashboard: realtime dashboard updates
   |-- WS /ws/agent: agent command channel
   v
Windows Agent App
```

The gateway is the only component that routes commands. Agents never accept inbound LAN connections; they initiate an outbound WebSocket session. This keeps firewall behavior simpler and makes the gateway the audit point.

## Consent Boundary

Sensitive handlers in the agent must call the local approval UI before doing work:

- screen screenshot/live
- webcam snapshot/live
- file download
- key-capture session
- power control

Approval decisions are reported to the backend and written to audit logs.

## Data Model

- `users`: dashboard operators.
- `agents`: enrolled devices.
- `enrollment_tokens`: one-time or reusable enrollment material.
- `commands`: command lifecycle and result summary.
- `audit_events`: security and operator activity log.

## Command Lifecycle

```text
queued -> sent -> pending_approval -> running -> succeeded
                                  \-> denied
         \-> failed
```

The backend persists each transition for auditability. If an agent disconnects mid-command, the command is marked failed.

