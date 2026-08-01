from __future__ import annotations

import json
import queue
import sys
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from remotectrl_agent.core.activity import ActivityCapture
from remotectrl_agent.core.client import AgentClient
from remotectrl_agent.core.config import AgentConfig, load_config, save_config
from remotectrl_agent.core.handlers import CommandHandlers



def hide_console_window() -> None:
    """Keep console stdio for Tauri IPC without leaving a visible terminal window."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, 0)
    except Exception:
        pass

class JsonBridge:
    """Line-delimited local IPC. Stdout is reserved for protocol messages."""

    def __init__(self) -> None:
        self._write_lock = threading.Lock()
        self._waiters: dict[str, queue.Queue[Any]] = {}
        self._waiters_lock = threading.Lock()
        self.on_request = None
        self.closed = threading.Event()

    def send(self, payload: dict[str, Any]) -> None:
        with self._write_lock:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()

    def event(self, name: str, data: dict[str, Any] | None = None) -> None:
        self.send({"type": "event", "event": name, "data": data or {}})

    def request_ui(self, method: str, params: dict[str, Any] | None = None, timeout: float = 90.0) -> Any:
        request_id = str(uuid.uuid4())
        waiter: queue.Queue[Any] = queue.Queue(maxsize=1)
        with self._waiters_lock:
            self._waiters[request_id] = waiter
        self.send({"type": "request", "id": request_id, "method": method, "params": params or {}})
        try:
            return waiter.get(timeout=timeout)
        except queue.Empty:
            return {"timeout": True}
        finally:
            with self._waiters_lock:
                self._waiters.pop(request_id, None)

    def resolve(self, request_id: str, value: Any) -> None:
        with self._waiters_lock:
            waiter = self._waiters.get(request_id)
        if waiter:
            try:
                waiter.put_nowait(value)
            except queue.Full:
                pass


class AgentSidecar:
    def __init__(self, bridge: JsonBridge, config: AgentConfig | None = None) -> None:
        self.bridge = bridge
        self.config = config or load_config()
        self.status = "Not connected"
        self.keycapture_text = ""
        self.logs: list[dict[str, str]] = []
        self.activity = ActivityCapture(self._activity_event)
        self.handlers = CommandHandlers(self.config, self._provider)
        self.client = AgentClient(self.config, self.handlers, self._on_status, self._approval, self._webcam_request, self._on_session_change, self._on_command_error)
        self._last_session_signature: tuple[bool, bool, bool, bool] | None = None
        if hasattr(self.bridge, "closed"):
            threading.Thread(target=self._monitor_sessions, daemon=True).start()

    def start_saved_connection(self) -> None:
        if self.config.agent_token:
            self._log("Saved enrollment is ready. Click Connect when you want this device online.")

    def _log(self, message: str, level: str = "info") -> None:
        entry = {"message": message, "level": level}
        self.logs = (self.logs + [entry])[-1000:]
        self.bridge.event("agent.log", entry)

    def _on_status(self, status: str) -> None:
        self.status = status
        self._log(status, "error" if status.lower().startswith("disconnected") else "info")
        if status == "Connected":
            self.client.publish_agent_event("agent_metadata", {"name": self.config.agent_name})
        self.bridge.event("agent.status", {"status": status, "state": self.state()})

    def _on_command_error(self, command_type: str, error: str) -> None:
        safe_error = str(error)[:500]
        self._log(f"{command_type} failed: {safe_error}", "error")
        self.bridge.event("agent.command_error", {"command_type": command_type, "error": safe_error})

    def _on_session_change(self) -> None:
        self._emit_session_state(force=True)

    def _monitor_sessions(self) -> None:
        while not self.bridge.closed.wait(0.5):
            self._emit_session_state()

    def _emit_session_state(self, force: bool = False, source: str = "remote") -> None:
        state = self.state()
        sessions = state["sessions"]
        signature = (bool(sessions["screen"]), bool(sessions["webcam"]), bool(sessions["activity"]), bool(sessions["keycapture"]))
        if not force and signature == self._last_session_signature:
            return
        self._last_session_signature = signature
        self.bridge.event("agent.session_state", {"state": state})
        self.client.publish_agent_event("agent_session_state", {"sessions": sessions, "source": source})
    def _activity_event(self, _event: str, data: dict[str, Any]) -> None:
        # Detailed events belong to the selected Web dashboard, not the Agent console.
        self.client.publish_activity_event(data)

    def _approval(self, message: dict[str, Any]) -> dict[str, Any]:
        response = self.bridge.request_ui("approval.request", {"message": message}, timeout=90)
        if not isinstance(response, dict) or response.get("timeout"):
            return {"approved": False, "approval_mode": "prompt_timeout", "policy_scope": "single_command"}
        return {
            "approved": bool(response.get("approved")),
            "approval_mode": str(response.get("approval_mode") or "prompt_once"),
            "policy_scope": str(response.get("policy_scope") or "single_command"),
        }

    def _webcam_request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.bridge.request_ui(f"webcam.{action}", payload, timeout=20)
        if not isinstance(response, dict) or response.get("timeout"):
            return {"error": "The local camera service did not respond. Keep RemoteCtrl Agent open and try again."}
        return response
    def _provider(self, action: str) -> Any:
        if action == "screen_capture_hide_approval":
            self.bridge.request_ui("capture.hide_approval_windows", {}, timeout=5)
            return "hidden"
        if action == "screen_capture_restore_approval":
            self.bridge.request_ui("capture.restore_approval_windows", {}, timeout=5)
            return "restored"
        if action == "start":
            self.bridge.event("keycapture.started", {})
            return "started"
        if action == "stop":
            self.bridge.event("keycapture.stopped", {})
            return "stopped"
        if action == "export":
            return self.keycapture_text
        if action == "activity_start":
            status = self.activity.start()
            self.bridge.event("activity.started", {"status": status})
            return status
        if action == "activity_stop":
            status = self.activity.stop()
            self.bridge.event("activity.stopped", {"status": status})
            return status
        if action == "activity_export":
            return self.activity.export()
        raise ValueError(f"Unknown local provider action: {action}")

    def _public_config(self) -> dict[str, Any]:
        data = asdict(self.config)
        data.pop("agent_token", None)
        data["enrolled"] = bool(self.config.agent_id and self.config.agent_token)
        return data

    def state(self) -> dict[str, Any]:
        return {
            "config": self._public_config(),
            "status": self.status,
            "sessions": {
                "screen": "screen" in self.client.active_streams,
                "webcam": self.client.webcam_active,
                "keycapture": self.client.keycapture_active,
                "activity": self.client.activity_active or self.activity.active,
            },
            "logs": self.logs,
        }

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "agent.get_state":
            return self.state()
        if method == "agent.update_config":
            previous_name = self.config.agent_name
            for key in ("server_url", "agent_name", "ui_theme"):
                value = params.get(key)
                if isinstance(value, str) and value.strip():
                    setattr(self.config, key, value.strip())
            save_config(self.config)
            if self.config.agent_name != previous_name:
                self.client.publish_agent_event("agent_metadata", {"name": self.config.agent_name})
                self._log("Agent name updated")
            self.bridge.event("agent.config", {"config": self._public_config(), "state": self.state()})
            return self.state()
        if method == "agent.enroll":
            token = str(params.get("enrollment_token") or "").strip()
            if not token:
                raise ValueError("Enrollment token is required")
            self.client.enroll(token)
            self._log("Device enrolled. Click Connect to take it online.")
            return self.state()
        if method == "agent.connect":
            self.config.paused = False
            save_config(self.config)
            self.client.start()
            self.status = "Connecting"
            self._log("Connection requested")
            self.bridge.event("agent.status", {"status": self.status, "state": self.state()})
            return self.state()
        if method == "agent.disconnect":
            self.config.paused = True
            save_config(self.config)
            self.client.stop()
            self.status = "Disconnected by local user"
            self._log("Disconnected from gateway by local user")
            self.bridge.event("agent.status", {"status": self.status, "state": self.state()})
            return self.state()
        if method == "agent.pause_toggle":
            self.config.paused = not self.config.paused
            save_config(self.config)
            if self.config.paused:
                self.client.stop()
            else:
                self.client.start()
            self.status = "Paused" if self.config.paused else "Connecting"
            self.bridge.event("agent.status", {"status": self.status, "state": self.state()})
            return self.state()
        if method == "agent.reset_approvals":
            self.client.reset_session_approvals()
            self._log("Session approvals reset")
            return self.state()
        if method == "agent.add_allowed_folder":
            folder = str(params.get("path") or "").strip()
            if not folder or not Path(folder).is_dir():
                raise ValueError("Choose an existing folder")
            if folder not in self.config.allowed_folders:
                self.config.allowed_folders.append(folder)
                save_config(self.config)
            self.bridge.event("agent.config", {"config": self._public_config(), "state": self.state()})
            return self.state()
        if method == "agent.remove_allowed_folder":
            folder = str(params.get("path") or "")
            self.config.allowed_folders = [item for item in self.config.allowed_folders if item != folder]
            save_config(self.config)
            self.bridge.event("agent.config", {"config": self._public_config(), "state": self.state()})
            self.client.publish_agent_event("agent_config_invalidated", {"kind": "allowed_folders"})
            self._log("Allowed folders changed")
            return self.state()
        if method == "agent.power_mode":
            self.config.dry_run_power = not bool(params.get("enabled"))
            save_config(self.config)
            self._log("Real power actions enabled" if not self.config.dry_run_power else "Real power actions disabled")
            state = self.state()
            self.bridge.event("agent.config", {"config": self._public_config(), "state": state})
            return state
        if method == "agent.set_theme":
            theme = "dark" if params.get("theme") == "dark" else "light"
            self.config.ui_theme = theme
            save_config(self.config)
            return self.state()
        if method == "webcam.frame":
            return self.client.publish_webcam_frame(str(params.get("frame") or ""), str(params.get("mime") or "image/jpeg"))
        if method == "webcam.error":
            self.client.fail_webcam_stream(str(params.get("error") or "Local camera capture failed"))
            return {"ok": True}
        if method == "keycapture.update":
            self.keycapture_text = str(params.get("text") or "")[-10000:]
            return {"ok": True}
        if method == "agent.activity_stop_local":
            status = self.activity.stop()
            self.client.activity_active = False
            self.bridge.event("activity.stopped", {"status": status, "local": True})
            self._emit_session_state(force=True, source="local")
            self._log("Activity capture stopped locally")
            return self.state()
        if method == "agent.shutdown":
            self.activity.stop()
            self.client.stop()
            self.bridge.closed.set()
            return {"ok": True}
        raise ValueError(f"Unknown bridge method: {method}")


def run() -> None:
    hide_console_window()
    bridge = JsonBridge()
    app = AgentSidecar(bridge)
    bridge.event("agent.ready", {"state": app.state()})
    app.start_saved_connection()
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "response":
                bridge.resolve(str(message.get("id")), message.get("result"))
                continue
            if kind != "request":
                continue
            request_id = str(message.get("id") or uuid.uuid4())
            try:
                result = app.dispatch(str(message.get("method")), message.get("params") or {})
                bridge.send({"type": "response", "id": request_id, "ok": True, "result": result})
            except Exception as exc:
                app._log(str(exc), "error")
                bridge.send({"type": "response", "id": request_id, "ok": False, "error": str(exc)})
            if bridge.closed.is_set():
                break
        except Exception as exc:
            bridge.send({"type": "event", "event": "agent.log", "data": {"message": str(exc), "level": "error"}})
    app.activity.stop()
    app.client.stop()


if __name__ == "__main__":
    run()
