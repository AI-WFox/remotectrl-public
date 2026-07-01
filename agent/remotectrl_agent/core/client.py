from __future__ import annotations

import json
import platform
import queue
import socket
import threading
import time
from typing import Any, Callable

import requests
import websocket

from remotectrl_agent.core.config import AgentConfig, save_config
from remotectrl_agent.core.handlers import CommandHandlers
from remotectrl_agent.core.protocol import http_to_ws, result


StatusCallback = Callable[[str], None]
ApprovalCallback = Callable[[dict], dict[str, Any] | bool]


class AgentClient:
    def __init__(
        self,
        config: AgentConfig,
        handlers: CommandHandlers,
        on_status: StatusCallback,
        request_approval: ApprovalCallback,
    ) -> None:
        self.config = config
        self.handlers = handlers
        self.on_status = on_status
        self.request_approval = request_approval
        self.outbox: queue.Queue[dict] = queue.Queue()
        self.stop_event = threading.Event()
        self.send_lock = threading.Lock()
        self.active_streams: dict[str, tuple[threading.Event, threading.Thread]] = {}
        self.session_approvals: set[str] = set()
        self.keycapture_active = False
        self.thread: threading.Thread | None = None

    def enroll(self, enrollment_token: str) -> None:
        response = requests.post(
            f"{self.config.server_url.rstrip('/')}/api/agents/enroll",
            json={
                "enrollment_token": enrollment_token,
                "name": self.config.agent_name,
                "hostname": socket.gethostname(),
                "os": platform.platform(),
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        self.config.agent_id = data["agent_id"]
        self.config.agent_token = data["agent_token"]
        save_config(self.config)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run_forever(self) -> None:
        while not self.stop_event.is_set():
            if self.config.paused:
                self.on_status("Paused")
                time.sleep(2)
                continue
            if not self.config.agent_token:
                self.on_status("Not enrolled")
                time.sleep(2)
                continue
            try:
                self._connect_once()
            except Exception as exc:
                self.on_status(f"Disconnected: {exc}")
                time.sleep(3)

    def _connect_once(self) -> None:
        ws_url = f"{http_to_ws(self.config.server_url.rstrip('/'))}/ws/agent?token={self.config.agent_token}"
        self.on_status("Connecting")
        ws = websocket.create_connection(ws_url, timeout=10)
        self.on_status("Connected")
        last_telemetry = 0.0
        try:
            while not self.stop_event.is_set() and not self.config.paused:
                now = time.time()
                if now - last_telemetry > 10:
                    ws.send(json.dumps({"type": "telemetry", "agent_id": self.config.agent_id}))
                    last_telemetry = now
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    break
                self._handle_message(ws, json.loads(raw))
        finally:
            self._stop_all_streams()
            self.session_approvals.clear()
            self.keycapture_active = False
            ws.close()

    def _handle_message(self, ws, message: dict) -> None:
        if message.get("type") != "command":
            return
        command_id = message["command_id"]
        agent_id = message["agent_id"]
        command_type = message["command_type"]
        payload = message.get("payload") or {}
        if command_type in {"screen.live.start", "webcam.live.start"} and self._stream_active(command_type):
            self._send(ws, result(command_id, agent_id, True, payload={"status": "already_running", "stream": self._stream_name(command_type)}))
            return
        if command_type == "keycapture.start" and self.keycapture_active:
            payload_result = self.handlers.handle(command_type, payload)
            payload_result["status"] = "already_running"
            self._send(ws, result(command_id, agent_id, True, payload=payload_result))
            return
        requires_approval = bool(message.get("requires_approval"))
        if requires_approval:
            decision = self._approval_decision(message)
            self._send(
                ws,
                {
                    "type": "approval_response",
                    "command_id": command_id,
                    "agent_id": agent_id,
                    "approved": decision["approved"],
                    "approval_mode": decision["approval_mode"],
                    "policy_scope": decision["policy_scope"],
                },
            )
            if not decision["approved"]:
                return
        try:
            if command_type in {"screen.live.start", "webcam.live.start"}:
                self._start_stream(ws, command_id, agent_id, command_type, payload)
                return
            if command_type in {"screen.live.stop", "webcam.live.stop"}:
                payload_result = self._stop_stream(command_type)
                self._send(ws, result(command_id, agent_id, True, payload=payload_result))
                return
            payload_result = self.handlers.handle(command_type, payload)
            if command_type == "keycapture.start" and payload_result.get("status") in {"started", "already_running"}:
                self.keycapture_active = True
            elif command_type == "keycapture.stop":
                self.keycapture_active = False
            self._send(ws, result(command_id, agent_id, True, payload=payload_result))
        except Exception as exc:
            self._send(ws, result(command_id, agent_id, False, error=str(exc)))


    def reset_session_approvals(self) -> None:
        self.session_approvals.clear()

    def _approval_decision(self, message: dict) -> dict[str, Any]:
        family = self._approval_family(message.get("command_type", ""))
        if family in self.session_approvals:
            return {"approved": True, "approval_mode": "session_cached", "policy_scope": "current_session"}
        raw = self.request_approval(message)
        if isinstance(raw, bool):
            return {"approved": raw, "approval_mode": "prompt_once", "policy_scope": "single_command"}
        approved = bool(raw.get("approved"))
        policy_scope = str(raw.get("policy_scope") or "single_command")
        if approved and policy_scope == "current_session":
            self.session_approvals.add(family)
        return {
            "approved": approved,
            "approval_mode": str(raw.get("approval_mode") or "prompt_once"),
            "policy_scope": policy_scope,
        }

    def _approval_family(self, command_type: str) -> str:
        if command_type.startswith("screen."):
            return "screen"
        if command_type.startswith("webcam."):
            return "webcam"
        if command_type.startswith("keycapture."):
            return "keycapture"
        if command_type.startswith("files."):
            return "files"
        if command_type.startswith("process."):
            return "processes"
        if command_type.startswith("app."):
            return "applications"
        if command_type.startswith("power."):
            return "power"
        return command_type

    def _stream_name(self, command_type: str) -> str:
        return "screen" if command_type.startswith("screen.") else "webcam"

    def _stream_active(self, command_type: str) -> bool:
        return self._stream_name(command_type) in self.active_streams
    def _start_stream(self, ws, command_id: str, agent_id: str, command_type: str, payload: dict) -> None:
        stream = self._stream_name(command_type)
        stop_stream = threading.Event()
        thread = threading.Thread(
            target=self._stream_frames,
            args=(ws, command_id, agent_id, stream, command_type, payload, stop_stream),
            daemon=True,
        )
        self.active_streams[stream] = (stop_stream, thread)
        thread.start()

    def _stop_stream(self, command_type: str) -> dict:
        stream = self._stream_name(command_type)
        active = self.active_streams.get(stream)
        if not active:
            return {"stream": stream, "status": "not_running"}
        stop_stream, thread = active
        stop_stream.set()
        if thread.is_alive():
            thread.join(timeout=2)
        self.active_streams.pop(stream, None)
        return {"stream": stream, "status": "stop_requested"}

    def _stop_all_streams(self) -> None:
        for stream, (stop_stream, thread) in list(self.active_streams.items()):
            stop_stream.set()
            if thread.is_alive():
                thread.join(timeout=2)
            self.active_streams.pop(stream, None)

    def _stream_frames(self, ws, command_id: str, agent_id: str, stream: str, command_type: str, payload: dict, stop_stream: threading.Event) -> None:
        fps = min(max(float(payload.get("fps", 10)), 1.0), 15.0)
        handler_type = "screen.screenshot" if command_type.startswith("screen.") else "webcam.snapshot"
        frame_count = 0
        started = time.time()
        self._send(ws, {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": stream, "status": "running", "fps": fps})
        try:
            while not stop_stream.is_set() and not self.stop_event.is_set():
                frame_started = time.time()
                frame = self.handlers.handle(handler_type, payload)
                image = frame.get("image")
                if image:
                    self._send(
                        ws,
                        {
                            "type": "stream_frame",
                            "command_id": command_id,
                            "agent_id": agent_id,
                            "stream": stream,
                            "mime": frame.get("mime", "image/jpeg"),
                            "frame": image,
                            "frame_index": frame_count + 1,
                            "sent_at": time.time(),
                        },
                    )
                    frame_count += 1
                elapsed = time.time() - frame_started
                stop_stream.wait(max(0.0, (1 / fps) - elapsed))
            self._send(
                ws,
                {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": stream, "status": "stopped", "fps": fps},
            )
            self._send(
                ws,
                result(
                    command_id,
                    agent_id,
                    True,
                    payload={"stream": stream, "frames": frame_count, "duration_seconds": round(time.time() - started, 2), "fps": fps},
                )
            )
        except Exception as exc:
            self._send(
                ws,
                {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": stream, "status": "failed", "fps": fps, "error": str(exc)},
            )
            self._send(ws, result(command_id, agent_id, False, error=str(exc)))
        finally:
            self.active_streams.pop(stream, None)

    def _send(self, ws, message: dict) -> None:
        with self.send_lock:
            ws.send(json.dumps(message))
