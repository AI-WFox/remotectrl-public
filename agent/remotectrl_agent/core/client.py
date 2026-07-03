from __future__ import annotations

import base64
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
        self.activity_active = False
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
            self.activity_active = False
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
        if command_type in {"keycapture.start", "activity.start"}:
            already_active = self.keycapture_active if command_type == "keycapture.start" else self.activity_active
            if already_active:
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
            elif command_type == "activity.start" and payload_result.get("status") in {"started", "already_running"}:
                self.activity_active = True
            elif command_type == "activity.stop":
                self.activity_active = False
            self._send(ws, result(command_id, agent_id, True, payload=payload_result))
        except Exception as exc:
            self._send(ws, result(command_id, agent_id, False, error=str(exc)))


    def reset_session_approvals(self) -> None:
        self.session_approvals.clear()

    def _approval_decision(self, message: dict) -> dict[str, Any]:
        command_type = message.get("command_type", "")
        family = self._approval_family(command_type)
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
        paired_session_actions = {
            "screen.live.start": "screen.live",
            "screen.live.stop": "screen.live",
            "webcam.live.start": "webcam.live",
            "webcam.live.stop": "webcam.live",
            "activity.start": "activity.session",
            "activity.stop": "activity.session",
            "keycapture.start": "keycapture.session",
            "keycapture.stop": "keycapture.session",
        }
        if command_type in paired_session_actions:
            return paired_session_actions[command_type]
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
        if stream == "webcam":
            self._stream_webcam_frames(ws, command_id, agent_id, payload, stop_stream)
            return
        fps = min(max(float(payload.get("fps", 10)), 1.0), 15.0)
        frame_count = 0
        started = time.time()
        stream_payload = dict(payload)
        stream_payload["_screen_hidden"] = True
        self._notify_handler_provider("screen_capture_hide")
        self._send(ws, {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": stream, "status": "running", "fps": fps})
        try:
            while not stop_stream.is_set() and not self.stop_event.is_set():
                frame_started = time.time()
                frame = self.handlers.handle("screen.screenshot", stream_payload)
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
            self._notify_handler_provider("screen_capture_restore")
            self.active_streams.pop(stream, None)

    def _stream_webcam_frames(self, ws, command_id: str, agent_id: str, payload: dict, stop_stream: threading.Event) -> None:
        fps = min(max(float(payload.get("fps", 15)), 1.0), 20.0)
        quality = min(max(int(payload.get("quality", 40)), 25), 85)
        width = int(payload.get("width", 640) or 640)
        height = int(payload.get("height", 360) or 360)
        camera_index = int(payload.get("camera_index", 0))
        frame_count = 0
        started = time.time()
        cap = None
        self._send(ws, {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": "webcam", "status": "running", "fps": fps})
        try:
            import cv2

            backend = getattr(cv2, "CAP_DSHOW", 0)
            cap = cv2.VideoCapture(camera_index, backend) if backend else cv2.VideoCapture(camera_index)
            if not cap.isOpened() and backend:
                cap.release()
                cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                raise RuntimeError(f"Camera {camera_index} is not available")
            if hasattr(cv2, "VideoWriter_fourcc") and hasattr(cv2, "CAP_PROP_FOURCC"):
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            while not stop_stream.is_set() and not self.stop_event.is_set():
                frame_started = time.time()
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Unable to read webcam frame")
                if width > 0 and height > 0:
                    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
                if not ok:
                    raise RuntimeError("Unable to encode webcam frame")
                self._send(
                    ws,
                    {
                        "type": "stream_frame",
                        "command_id": command_id,
                        "agent_id": agent_id,
                        "stream": "webcam",
                        "mime": "image/jpeg",
                        "frame": base64.b64encode(encoded.tobytes()).decode(),
                        "frame_index": frame_count + 1,
                        "sent_at": time.time(),
                    },
                )
                frame_count += 1
                elapsed = time.time() - frame_started
                stop_stream.wait(max(0.0, (1 / fps) - elapsed))
            self._send(ws, {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": "webcam", "status": "stopped", "fps": fps})
            self._send(
                ws,
                result(
                    command_id,
                    agent_id,
                    True,
                    payload={"stream": "webcam", "frames": frame_count, "duration_seconds": round(time.time() - started, 2), "fps": fps, "width": width, "height": height, "quality": quality},
                ),
            )
        except Exception as exc:
            self._send(ws, {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": "webcam", "status": "failed", "fps": fps, "error": str(exc)})
            self._send(ws, result(command_id, agent_id, False, error=str(exc)))
        finally:
            if cap is not None:
                cap.release()
            self.active_streams.pop("webcam", None)

    def _notify_handler_provider(self, action: str) -> None:
        provider = getattr(self.handlers, "keycapture_provider", None)
        if callable(provider):
            provider(action)

    def _send(self, ws, message: dict) -> None:
        with self.send_lock:
            ws.send(json.dumps(message))
