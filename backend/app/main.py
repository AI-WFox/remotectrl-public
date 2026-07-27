from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.db import init_db
from app.core.security import make_token, verify_password
from app.dependencies import get_repository, require_user
from app.schemas import (
    AgentEnrollment,
    AgentEnrollmentResponse,
    AgentPublic,
    AuditEventPublic,
    CommandCreate,
    CommandPublic,
    EnrollmentTokenCreate,
    EnrollmentTokenResponse,
    LoginRequest,
    LoginResponse,
)
from app.services.repository import Repository, command_requires_approval
from app.services.session_manager import SessionManager


settings = get_settings()
manager = SessionManager()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_DIST = PROJECT_ROOT / "web" / "dist"

COMMAND_CATALOG = [
    {"type": "app.list", "label": "List applications"},
    {"type": "app.start", "label": "Start application"},
    {"type": "app.stop", "label": "Stop application"},
    {"type": "process.list", "label": "List processes"},
    {"type": "process.kill", "label": "Kill process"},
    {"type": "screen.screenshot", "label": "Capture screenshot"},
    {"type": "screen.live.start", "label": "Start screen stream"},
    {"type": "screen.live.stop", "label": "Stop screen stream"},
    {"type": "files.roots", "label": "List allowed file roots"},
    {"type": "files.list", "label": "Browse allowed files"},
    {"type": "files.download", "label": "Download file"},
    {"type": "webcam.list", "label": "List cameras"},
    {"type": "webcam.snapshot", "label": "Capture webcam snapshot"},
    {"type": "webcam.live.start", "label": "Start webcam stream"},
    {"type": "webcam.live.stop", "label": "Stop webcam stream"},
    {"type": "power.shutdown", "label": "Shutdown endpoint"},
    {"type": "power.restart", "label": "Restart endpoint"},
    {"type": "power.logout", "label": "Logout endpoint"},
    {"type": "power.sleep", "label": "Sleep endpoint"},
    {"type": "power.status", "label": "Read power status"},
    {"type": "keycapture.start", "label": "Start visible key-capture session"},
    {"type": "keycapture.stop", "label": "Stop visible key-capture session"},
    {"type": "keycapture.export", "label": "Export visible key-capture session"},
    {"type": "activity.start", "label": "Start visible activity capture session"},
    {"type": "activity.stop", "label": "Stop visible activity capture session"},
    {"type": "activity.export", "label": "Export visible activity capture session"},
]
for item in COMMAND_CATALOG:
    item["requires_approval"] = command_requires_approval(item["type"])

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin(settings.default_admin_email, settings.default_admin_password)
    if not repo.list_agents():
        token_record, token = repo.create_enrollment_token("Initial demo enrollment", reusable=True)
        repo.audit(
            "system",
            "enrollment_token.created",
            detail={"id": token_record["id"], "label": token_record["label"], "demo_token": token},
        )
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "remotectrl-gateway"}


@app.get("/api/bootstrap")
def bootstrap() -> dict:
    return {
        "service": settings.app_name,
        "demo_admin_email": settings.default_admin_email,
        "capabilities": COMMAND_CATALOG,
        "safety": {
            "local_approval_required": True,
            "stealth_mode": False,
            "power_dry_run_default": True,
        },
    }


@app.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, repo: Repository = Depends(get_repository)) -> LoginResponse:
    user = repo.find_user_by_email(body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = make_token(
        {"kind": "user", "sub": user["id"], "email": user["email"], "role": user["role"]},
        settings.secret_key,
    )
    repo.audit(user["email"], "auth.login")
    return LoginResponse(access_token=access_token)


@app.post("/api/enrollment-tokens", response_model=EnrollmentTokenResponse)
def create_enrollment_token(
    body: EnrollmentTokenCreate,
    user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> EnrollmentTokenResponse:
    record, token = repo.create_enrollment_token(body.label, body.reusable)
    repo.audit(user["email"], "enrollment_token.created", detail={"id": record["id"], "label": body.label})
    return EnrollmentTokenResponse(
        id=record["id"],
        token=token,
        label=record["label"],
        reusable=bool(record["reusable"]),
        created_at=record["created_at"],
    )


@app.post("/api/agents/enroll", response_model=AgentEnrollmentResponse)
def enroll_agent(
    body: AgentEnrollment,
    request: Request,
    repo: Repository = Depends(get_repository),
) -> AgentEnrollmentResponse:
    ip_address = request.client.host if request.client else None
    result = repo.enroll_agent(body.enrollment_token, body.name, body.hostname, body.os, ip_address)
    if result is None:
        raise HTTPException(status_code=403, detail="Invalid enrollment token")
    agent, agent_token = result
    repo.audit("agent-enrollment", "agent.enrolled", agent_id=agent["id"], detail={"name": body.name})
    return AgentEnrollmentResponse(agent_id=agent["id"], agent_token=agent_token)


@app.get("/api/agents", response_model=list[AgentPublic])
def list_agents(
    _user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> list[dict]:
    agents = repo.list_agents()
    for agent in agents:
        if manager.is_agent_online(agent["id"]):
            agent["status"] = "online"
    return agents


@app.delete("/api/agents/offline")
async def delete_offline_agents(
    user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> dict[str, int]:
    deleted = repo.delete_offline_agents(set(manager.agent_sockets.keys()))
    repo.audit(user["email"], "agents.offline_deleted", detail={"deleted": deleted})
    await manager.broadcast_dashboard({"type": "agents.cleaned", "deleted": deleted})
    return {"deleted": deleted}


@app.delete("/api/agents/{agent_id}")
async def delete_agent(
    agent_id: str,
    user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> dict[str, bool | str]:
    agent = repo.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    was_online = manager.is_agent_online(agent_id)
    await manager.close_agent(agent_id, reason="Removed by operator")
    deleted = repo.delete_agent(agent_id)
    repo.audit(
        user["email"],
        "agent.deleted",
        detail={"agent_id": agent_id, "name": agent["name"], "online": was_online},
    )
    await manager.broadcast_dashboard({"type": "agent.deleted", "agent_id": agent_id})
    return {"deleted": deleted, "agent_id": agent_id}


@app.post("/api/commands", response_model=CommandPublic)
async def create_command(
    body: CommandCreate,
    user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> dict:
    try:
        command = repo.create_command(body.agent_id, body.type, body.payload, user["email"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    sent = await manager.send_to_agent(
        body.agent_id,
        {
            "type": "command",
            "command_id": command["id"],
            "agent_id": body.agent_id,
            "command_type": body.type,
            "payload": body.payload,
            "requires_approval": command["requires_approval"],
        },
    )
    if sent and command["requires_approval"]:
        next_status = "pending_approval"
    elif sent:
        next_status = "sent"
    else:
        next_status = "failed"
    command = repo.update_command(command["id"], next_status, error=None if sent else "Agent offline")
    repo.audit(user["email"], "command.created", body.agent_id, command["id"], {"type": body.type, "sent": sent})
    await manager.broadcast_dashboard({"type": "command.updated", "command": command})
    return command


@app.get("/api/commands", response_model=list[CommandPublic])
def list_commands(
    _user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> list[dict]:
    return repo.list_commands()


@app.get("/api/agents/{agent_id}/commands", response_model=list[CommandPublic])
def list_agent_commands(
    agent_id: str,
    _user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> list[dict]:
    if not repo.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return repo.list_agent_commands(agent_id)


@app.get("/api/audit", response_model=list[AuditEventPublic])
def list_audit(
    _user: dict[str, str] = Depends(require_user),
    repo: Repository = Depends(get_repository),
) -> list[dict]:
    return repo.list_audit()


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    await manager.connect_dashboard(websocket)
    try:
        await websocket.send_json({"type": "hello", "role": "dashboard"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_dashboard(websocket)


@app.websocket("/ws/agent")
async def agent_ws(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    repo = Repository(settings.database_path)
    agent = repo.find_agent_by_token(token or "")
    if not agent:
        await websocket.close(code=4403)
        return
    await manager.connect_agent(agent["id"], websocket)
    repo.set_agent_status(agent["id"], "online", websocket.client.host if websocket.client else None)
    repo.audit("agent", "agent.connected", agent_id=agent["id"])
    await manager.broadcast_dashboard({"type": "agent.online", "agent_id": agent["id"]})
    try:
        await websocket.send_json({"type": "hello", "role": "agent", "agent_id": agent["id"]})
        while True:
            message = await websocket.receive_json()
            await handle_agent_message(repo, agent["id"], message)
    except WebSocketDisconnect:
        pass
    finally:
        disconnected_current_socket = manager.disconnect_agent(agent["id"], websocket)
        if disconnected_current_socket and repo.get_agent(agent["id"]):
            repo.set_agent_status(agent["id"], "offline")
            repo.audit("agent", "agent.disconnected", agent_id=agent["id"])
            await manager.broadcast_dashboard({"type": "agent.offline", "agent_id": agent["id"]})


async def handle_agent_message(repo: Repository, agent_id: str, message: dict) -> None:
    message_type = message.get("type")
    command_id = message.get("command_id")
    if message_type == "approval_response":
        if not command_id:
            return
        approved = bool(message.get("approved"))
        status = "running" if approved else "denied"
        command = repo.update_command(command_id, status, error=None if approved else "Denied locally")
        repo.audit("agent", "approval.response", agent_id, command_id, {"approved": approved, "approval_mode": message.get("approval_mode", "prompt_once"), "policy_scope": message.get("policy_scope", "single_command")})
        await manager.broadcast_dashboard({"type": "command.updated", "command": command})
    elif message_type == "command_result":
        if not command_id:
            return
        ok = bool(message.get("ok"))
        command = repo.update_command(
            command_id,
            "succeeded" if ok else "failed",
            result=message.get("payload") if ok else None,
            error=message.get("error"),
        )
        repo.audit("agent", "command.result", agent_id, command_id, {"ok": ok})
        await manager.broadcast_dashboard({"type": "command.updated", "command": command})
    elif message_type == "activity_event":
        event = message.get("event")
        if not isinstance(event, dict):
            return
        await manager.broadcast_dashboard({"type": "activity.event", "agent_id": agent_id, "event": event})
        return
    elif message_type == "stream_frame":
        await manager.broadcast_dashboard({**message, "type": "stream.frame", "agent_id": agent_id})
        return
    elif message_type == "stream_status":
        status = str(message.get("status") or "unknown")
        stream = message.get("stream")
        action = {
            "running": "stream.started",
            "stopped": "stream.stopped",
            "failed": "stream.error",
        }.get(status, "stream.status")
        repo.audit(
            "agent",
            action,
            agent_id,
            command_id,
            {"stream": stream, "status": status, "fps": message.get("fps"), "error": message.get("error")},
        )
        await manager.broadcast_dashboard({**message, "type": "stream.status", "agent_id": agent_id})
        return
    elif message_type == "telemetry":
        repo.set_agent_status(agent_id, "online")
    await manager.broadcast_dashboard({"type": "agent.message", "agent_id": agent_id, "message": message})


if WEB_DIST.exists():
    assets_dir = WEB_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_dashboard(full_path: str) -> FileResponse:
        candidate = WEB_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
