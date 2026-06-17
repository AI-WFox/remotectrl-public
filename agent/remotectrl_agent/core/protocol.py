from __future__ import annotations

from typing import Any


def http_to_ws(url: str) -> str:
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url


def result(command_id: str, agent_id: str, ok: bool, payload: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    return {
        "type": "command_result",
        "command_id": command_id,
        "agent_id": agent_id,
        "ok": ok,
        "payload": payload or {},
        "error": error,
    }

