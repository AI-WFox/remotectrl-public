from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.db import session
from app.core.security import generate_secret, hash_password


APPROVAL_REQUIRED_COMMANDS = {
    "app.list",
    "app.start",
    "app.stop",
    "process.list",
    "process.kill",
    "files.roots",
    "files.list",
    "files.download",
    "screen.live.start",
    "screen.live.stop",
    "screen.screenshot",
    "webcam.live.start",
    "webcam.live.stop",
    "webcam.list",
    "webcam.snapshot",
    "activity.start",
    "activity.stop",
    "activity.export",
    "power.shutdown",
    "power.restart",
    "power.sleep",
    "power.status",
}


def command_requires_approval(command_type: str) -> bool:
    return command_type in APPROVAL_REQUIRED_COMMANDS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Repository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def ensure_admin(self, email: str, password: str) -> None:
        with session(self.database_path) as conn:
            existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE users SET password_hash=?, role='admin' WHERE id=?",
                    (hash_password(password), existing["id"]),
                )
                return
            conn.execute(
                "INSERT INTO users (id,email,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4()), email, hash_password(password), "admin", now_iso()),
            )

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        with session(self.database_path) as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            return row_to_dict(row) if row else None

    def create_enrollment_token(self, label: str, reusable: bool) -> tuple[dict[str, Any], str]:
        token = generate_secret("enroll")
        record = {
            "id": str(uuid.uuid4()),
            "token_hash": token_hash(token),
            "label": label,
            "reusable": 1 if reusable else 0,
            "created_at": now_iso(),
        }
        with session(self.database_path) as conn:
            conn.execute(
                "INSERT INTO enrollment_tokens (id,token_hash,label,reusable,created_at) VALUES (:id,:token_hash,:label,:reusable,:created_at)",
                record,
            )
        return record, token

    def consume_enrollment(self, token: str) -> dict[str, Any] | None:
        hashed = token_hash(token)
        with session(self.database_path) as conn:
            row = conn.execute("SELECT * FROM enrollment_tokens WHERE token_hash=?", (hashed,)).fetchone()
            if not row:
                return None
            record = row_to_dict(row)
            if record["used_at"] and not record["reusable"]:
                return None
            if not record["reusable"]:
                conn.execute("UPDATE enrollment_tokens SET used_at=? WHERE id=?", (now_iso(), record["id"]))
            return record

    def enroll_agent(self, enrollment_token: str, name: str, hostname: str, os_name: str, ip_address: str | None) -> tuple[dict[str, Any], str] | None:
        if not self.consume_enrollment(enrollment_token):
            return None
        agent_token = generate_secret("agent")
        existing = self.find_agent_by_identity(name, hostname)
        if existing:
            with session(self.database_path) as conn:
                conn.execute(
                    "UPDATE agents SET token_hash=?, os=?, ip_address=COALESCE(?, ip_address), status='offline', last_seen_at=NULL WHERE id=?",
                    (token_hash(agent_token), os_name, ip_address, existing["id"]),
                )
            return self.get_agent(existing["id"]), agent_token
        record = {
            "id": str(uuid.uuid4()),
            "name": name,
            "token_hash": token_hash(agent_token),
            "os": os_name,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "offline",
            "last_seen_at": None,
            "created_at": now_iso(),
        }
        with session(self.database_path) as conn:
            conn.execute(
                "INSERT INTO agents (id,name,token_hash,os,hostname,ip_address,status,last_seen_at,created_at) VALUES (:id,:name,:token_hash,:os,:hostname,:ip_address,:status,:last_seen_at,:created_at)",
                record,
            )
        return record, agent_token

    def find_agent_by_identity(self, name: str, hostname: str) -> dict[str, Any] | None:
        with session(self.database_path) as conn:
            row = conn.execute(
                "SELECT * FROM agents WHERE name=? AND hostname=? ORDER BY created_at DESC LIMIT 1",
                (name, hostname),
            ).fetchone()
            return row_to_dict(row) if row else None

    def find_agent_by_token(self, agent_token: str) -> dict[str, Any] | None:
        with session(self.database_path) as conn:
            row = conn.execute("SELECT * FROM agents WHERE token_hash=?", (token_hash(agent_token),)).fetchone()
            return row_to_dict(row) if row else None

    def set_agent_status(self, agent_id: str, status: str, ip_address: str | None = None) -> None:
        with session(self.database_path) as conn:
            conn.execute(
                "UPDATE agents SET status=?, ip_address=COALESCE(?, ip_address), last_seen_at=? WHERE id=?",
                (status, ip_address, now_iso(), agent_id),
            )

    def update_agent_name(self, agent_id: str, name: str) -> dict[str, Any]:
        normalized = name.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Agent name must contain 1 to 128 characters")
        with session(self.database_path) as conn:
            conn.execute("UPDATE agents SET name=? WHERE id=?", (normalized, agent_id))
        agent = self.get_agent(agent_id)
        if not agent:
            raise KeyError(agent_id)
        return agent
    def list_agents(self) -> list[dict[str, Any]]:
        with session(self.database_path) as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
            return [row_to_dict(row) for row in rows]

    def delete_offline_agents(self, keep_agent_ids: set[str] | None = None) -> int:
        keep_agent_ids = keep_agent_ids or set()
        with session(self.database_path) as conn:
            rows = conn.execute("SELECT id FROM agents").fetchall()
            agent_ids = [row["id"] for row in rows if row["id"] not in keep_agent_ids]
            return self._delete_agents(conn, agent_ids)

    def delete_agent(self, agent_id: str) -> bool:
        with session(self.database_path) as conn:
            return self._delete_agents(conn, [agent_id]) == 1

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with session(self.database_path) as conn:
            row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
            return row_to_dict(row) if row else None

    def find_active_duplicate_command(
        self,
        agent_id: str,
        command_type: str,
        payload: dict[str, Any],
        created_by: str,
    ) -> dict[str, Any] | None:
        active_statuses = ("queued", "sent", "pending_approval", "running")
        placeholders = ",".join("?" for _ in active_statuses)
        with session(self.database_path) as conn:
            rows = conn.execute(
                f"""SELECT * FROM commands
                    WHERE agent_id=? AND type=? AND created_by=?
                      AND status IN ({placeholders})
                    ORDER BY created_at DESC LIMIT 25""",
                (agent_id, command_type, created_by, *active_statuses),
            ).fetchall()
        for row in rows:
            decoded = self._decode_command(row_to_dict(row))
            if decoded.get("payload") == payload:
                return decoded
        return None

    def fail_active_commands_for_agent(self, agent_id: str, error: str = "Agent disconnected") -> list[dict[str, Any]]:
        active_statuses = ("queued", "sent", "pending_approval", "running")
        placeholders = ",".join("?" for _ in active_statuses)
        updated_at = now_iso()
        with session(self.database_path) as conn:
            rows = conn.execute(
                f"SELECT id FROM commands WHERE agent_id=? AND status IN ({placeholders})",
                (agent_id, *active_statuses),
            ).fetchall()
            command_ids = [str(row["id"]) for row in rows]
            if command_ids:
                ids = ",".join("?" for _ in command_ids)
                conn.execute(
                    f"UPDATE commands SET status='failed', error=?, updated_at=? WHERE id IN ({ids})",
                    (error, updated_at, *command_ids),
                )
        return [self.get_command(command_id) for command_id in command_ids]

    def create_command(self, agent_id: str, command_type: str, payload: dict[str, Any], created_by: str) -> dict[str, Any]:
        if not self.get_agent(agent_id):
            raise KeyError(f"Unknown agent: {agent_id}")
        created = now_iso()
        requires_approval = command_requires_approval(command_type)
        record = {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "type": command_type,
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            "requires_approval": 1 if requires_approval else 0,
            "status": "queued",
            "result": None,
            "error": None,
            "created_by": created_by,
            "created_at": created,
            "updated_at": created,
        }
        with session(self.database_path) as conn:
            conn.execute(
                "INSERT INTO commands (id,agent_id,type,payload,requires_approval,status,result,error,created_by,created_at,updated_at) VALUES (:id,:agent_id,:type,:payload,:requires_approval,:status,:result,:error,:created_by,:created_at,:updated_at)",
                record,
            )
        return self.get_command(record["id"])

    def get_command(self, command_id: str) -> dict[str, Any]:
        with session(self.database_path) as conn:
            row = conn.execute("SELECT * FROM commands WHERE id=?", (command_id,)).fetchone()
            if not row:
                raise KeyError(command_id)
            return self._decode_command(row_to_dict(row))

    def list_commands(self, limit: int = 50) -> list[dict[str, Any]]:
        with session(self.database_path) as conn:
            rows = conn.execute("SELECT * FROM commands ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [self._decode_command(row_to_dict(row)) for row in rows]

    def list_agent_commands(self, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with session(self.database_path) as conn:
            rows = conn.execute(
                "SELECT * FROM commands WHERE agent_id=? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
            return [self._decode_command(row_to_dict(row)) for row in rows]

    def update_command(self, command_id: str, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
        with session(self.database_path) as conn:
            conn.execute(
                "UPDATE commands SET status=?, result=?, error=?, updated_at=? WHERE id=?",
                (status, json.dumps(result) if result is not None else None, error, now_iso(), command_id),
            )
        return self.get_command(command_id)

    def audit(self, actor: str, action: str, agent_id: str | None = None, command_id: str | None = None, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {
            "id": str(uuid.uuid4()),
            "actor": actor,
            "action": action,
            "agent_id": agent_id,
            "command_id": command_id,
            "detail": json.dumps(detail or {}),
            "created_at": now_iso(),
        }
        with session(self.database_path) as conn:
            conn.execute(
                "INSERT INTO audit_events (id,actor,action,agent_id,command_id,detail,created_at) VALUES (:id,:actor,:action,:agent_id,:command_id,:detail,:created_at)",
                record,
            )
        return self._decode_audit(record)

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with session(self.database_path) as conn:
            rows = conn.execute("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [self._decode_audit(row_to_dict(row)) for row in rows]

    def _delete_agents(self, conn: Any, agent_ids: list[str]) -> int:
        if not agent_ids:
            return 0
        placeholders = ",".join("?" for _ in agent_ids)
        existing_rows = conn.execute(f"SELECT id FROM agents WHERE id IN ({placeholders})", agent_ids).fetchall()
        existing_ids = [row["id"] for row in existing_rows]
        if not existing_ids:
            return 0
        placeholders = ",".join("?" for _ in existing_ids)
        command_rows = conn.execute(
            f"SELECT id FROM commands WHERE agent_id IN ({placeholders})",
            existing_ids,
        ).fetchall()
        command_ids = [row["id"] for row in command_rows]
        if command_ids:
            command_placeholders = ",".join("?" for _ in command_ids)
            conn.execute(
                f"DELETE FROM audit_events WHERE command_id IN ({command_placeholders})",
                command_ids,
            )
            conn.execute(
                f"DELETE FROM commands WHERE id IN ({command_placeholders})",
                command_ids,
            )
        conn.execute(f"DELETE FROM audit_events WHERE agent_id IN ({placeholders})", existing_ids)
        conn.execute(f"DELETE FROM agents WHERE id IN ({placeholders})", existing_ids)
        return len(existing_ids)

    def _decode_command(self, record: dict[str, Any]) -> dict[str, Any]:
        record["payload"] = json.loads(record["payload"] or "{}")
        record["result"] = json.loads(record["result"]) if record.get("result") else None
        record["requires_approval"] = bool(record["requires_approval"])
        return record

    def _decode_audit(self, record: dict[str, Any]) -> dict[str, Any]:
        record["detail"] = json.loads(record["detail"] or "{}")
        return record
