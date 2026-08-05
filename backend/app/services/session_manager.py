from __future__ import annotations

import hashlib
import secrets
import time
import threading
from typing import Any
from fastapi import WebSocket


class SessionManager:
    def __init__(self) -> None:
        self.agent_sockets: dict[str, WebSocket] = {}
        self.dashboard_sockets: set[WebSocket] = set()
        self.agent_sessions: dict[str, dict[str, bool]] = {}
        self.dashboard_tickets: dict[str, float] = {}
        self.dashboard_ticket_lock = threading.Lock()

    def issue_dashboard_ticket(self, ttl_seconds: int = 30) -> str:
        now = time.time()
        ticket = secrets.token_urlsafe(32)
        with self.dashboard_ticket_lock:
            self.dashboard_tickets = {
                digest: expires_at
                for digest, expires_at in self.dashboard_tickets.items()
                if expires_at > now
            }
            self.dashboard_tickets[hashlib.sha256(ticket.encode()).hexdigest()] = now + ttl_seconds
        return ticket

    def consume_dashboard_ticket(self, ticket: str) -> bool:
        digest = hashlib.sha256(ticket.encode()).hexdigest()
        with self.dashboard_ticket_lock:
            expires_at = self.dashboard_tickets.pop(digest, None)
        return expires_at is not None and expires_at > time.time()

    async def connect_agent(self, agent_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self.attach_agent(agent_id, websocket)

    def attach_agent(self, agent_id: str, websocket: WebSocket) -> None:
        """Register an Agent socket that has already completed its handshake."""
        self.agent_sockets[agent_id] = websocket

    def disconnect_agent(self, agent_id: str, websocket: WebSocket) -> bool:
        """Remove an agent only when its current socket is the one closing.

        An Agent can reconnect before the previous WebSocket has finished its
        disconnect handler. Without this identity check, the old handler can
        remove the new, healthy connection and incorrectly mark it offline.
        """
        if self.agent_sockets.get(agent_id) is not websocket:
            return False
        self.agent_sockets.pop(agent_id, None)
        return True

    async def close_agent(self, agent_id: str, code: int = 4400, reason: str = "Agent removed") -> bool:
        websocket = self.agent_sockets.pop(agent_id, None)
        if websocket is None:
            return False
        try:
            await websocket.close(code=code, reason=reason)
        except Exception:
            pass
        return True

    async def connect_dashboard(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.dashboard_sockets.add(websocket)

    def disconnect_dashboard(self, websocket: WebSocket) -> None:
        self.dashboard_sockets.discard(websocket)

    def set_agent_sessions(self, agent_id: str, sessions: dict[str, bool]) -> None:
        self.agent_sessions[agent_id] = {key: bool(sessions.get(key)) for key in ("screen", "webcam", "activity")}

    def clear_agent_sessions(self, agent_id: str) -> None:
        self.agent_sessions[agent_id] = {"screen": False, "webcam": False, "activity": False}

    def session_snapshot(self) -> dict[str, dict[str, bool]]:
        return dict(self.agent_sessions)

    def is_agent_online(self, agent_id: str) -> bool:
        return agent_id in self.agent_sockets

    async def send_to_agent(self, agent_id: str, message: dict[str, Any]) -> bool:
        websocket = self.agent_sockets.get(agent_id)
        if websocket is None:
            return False
        await websocket.send_json(message)
        return True

    async def broadcast_dashboard(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for websocket in self.dashboard_sockets:
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect_dashboard(websocket)
