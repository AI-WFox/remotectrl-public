from __future__ import annotations

import json
import platform
import queue
import socket
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

import requests
import websocket

from remotectrl_agent.core.config import AgentConfig, save_config
from remotectrl_agent.core.handlers import CommandHandlers
from remotectrl_agent.core.protocol import http_to_ws, result


StatusCallback = Callable[[str], None]
ApprovalCallback = Callable[[dict], dict[str, Any] | bool]
WebcamCallback = Callable[[str, dict[str, Any]], dict[str, Any]]
SessionCallback = Callable[[], None]
CommandErrorCallback = Callable[[str, str], None]


class AgentClient:
    def __init__(
        self,
        config: AgentConfig,
        handlers: CommandHandlers,
        on_status: StatusCallback,
        request_approval: ApprovalCallback,
        webcam_provider: WebcamCallback | None = None,
        on_session_change: SessionCallback | None = None,
        on_command_error: CommandErrorCallback | None = None,
    ) -> None:
        self.config = config
        self.handlers = handlers
        self.on_status = on_status
        self.request_approval = request_approval
        self.webcam_provider = webcam_provider
        self.on_session_change = on_session_change
        self.on_command_error = on_command_error
        self.webcam_stream: dict[str, Any] | None = None
        self.active_ws: Any | None = None
        self.outbox: queue.Queue[dict] = queue.Queue()
        self.stop_event = threading.Event()
        self.send_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.active_streams: dict[str, tuple[threading.Event, threading.Thread]] = {}
        self.session_approvals: set[str] = set()
        self.activity_active = False
        self.command_slots = threading.BoundedSemaphore(8)
        self.thread: threading.Thread | None = None

    def enroll(self, enrollment_token: str) -> None:
        endpoint = f"{self.config.server_url.rstrip('/')}/api/agents/enroll"
        try:
            response = requests.post(
                endpoint,
                json={
                    "enrollment_token": enrollment_token,
                    "name": self.config.agent_name,
                    "hostname": socket.gethostname(),
                    "os": platform.platform(),
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            hostname = (urlparse(self.config.server_url).hostname or "").lower()
            if hostname in {"127.0.0.1", "localhost", "::1"}:
                raise RuntimeError(
                    "Gateway URL points to this Agent machine. For the public demo, enter the Render HTTPS URL "
                    "(for example https://remotectrl-public-demo.onrender.com) in Settings, save it, then enroll again."
                ) from exc
            raise RuntimeError(f"Cannot reach the Gateway. Check the Gateway URL and internet connection. ({exc})") from exc

        if response.status_code == 403:
            raise ValueError("Enrollment token is invalid or has already been used. Create a new enrollment token in the dashboard.")
        if not response.ok:
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except ValueError:
                detail = response.text.strip()
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"Gateway rejected enrollment ({response.status_code}){suffix}")

        data = response.json()
        self.config.agent_id = data["agent_id"]
        self.config.agent_token = data["agent_token"]
        save_config(self.config)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            if not self.stop_event.is_set():
                return
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.state_lock:
            ws = self.active_ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _run_forever(self) -> None:
        """Reconnect only after this device was explicitly connected by its local user."""
        if self.config.paused:
            self.on_status("Paused")
            return
        if not self.config.agent_token:
            self.on_status("Not enrolled")
            return

        delays = (1, 2, 5, 10, 20, 30)
        attempt = 0
        while not self.stop_event.is_set() and not self.config.paused:
            try:
                self._connect_once()
                attempt = 0
            except Exception as exc:
                if self.stop_event.is_set() or self.config.paused:
                    break
                delay = delays[min(attempt, len(delays) - 1)]
                self.on_status(f"Reconnecting in {delay}s: {exc}")
                attempt += 1
                if self.stop_event.wait(delay):
                    break
                continue

            if self.stop_event.is_set() or self.config.paused:
                break

            # A clean socket close is still unexpected unless the local user stopped it.
            delay = delays[min(attempt, len(delays) - 1)]
            self.on_status(f"Reconnecting in {delay}s: gateway connection closed")
            attempt += 1
            if self.stop_event.wait(delay):
                break

    def _connect_once(self) -> None:
        ws_url = f"{http_to_ws(self.config.server_url.rstrip('/'))}/ws/agent"
        self.on_status("Connecting")
        ws = websocket.create_connection(ws_url, timeout=10)
        ws.send(json.dumps({"type": "authenticate", "token": self.config.agent_token}))
        hello = json.loads(ws.recv())
        if hello.get("type") != "hello" or hello.get("role") != "agent":
            ws.close()
            raise RuntimeError("Gateway rejected Agent authentication")
        with self.state_lock:
            self.active_ws = ws
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
                message = json.loads(raw)
                if message.get("type") != "command":
                    continue
                if not self.command_slots.acquire(blocking=False):
                    self._send(
                        ws,
                        result(
                            str(message.get("command_id") or ""),
                            str(message.get("agent_id") or self.config.agent_id or ""),
                            False,
                            error="Agent is busy processing other requests",
                        ),
                    )
                    continue
                threading.Thread(target=self._handle_message_bounded, args=(ws, message), daemon=True).start()
        finally:
            self._stop_all_streams()
            # Local-visible capture must never continue after the authenticated gateway session ends.
            try:
                if self.activity_active:
                    self.handlers.handle("activity.stop", {})
            except Exception:
                pass
            with self.state_lock:
                self.active_ws = None
                self.session_approvals.clear()
                self.activity_active = False
            self._notify_session_change()
            ws.close()
            if not self.stop_event.is_set() and not self.config.paused:
                self.on_status("Disconnected: gateway connection closed")

    def publish_activity_event(self, event: dict[str, Any]) -> bool:
        """Relay visible-session activity to the authenticated dashboard in real time."""
        with self.state_lock:
            ws = self.active_ws
            agent_id = self.config.agent_id
        if not ws or not agent_id:
            return False
        try:
            self._send(ws, {"type": "activity_event", "agent_id": agent_id, "event": event})
            return True
        except Exception:
            return False

    def _handle_message_bounded(self, ws, message: dict) -> None:
        try:
            self._handle_message(ws, message)
        finally:
            self.command_slots.release()

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
        if command_type == "activity.start":
            with self.state_lock:
                already_active = self.activity_active
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
            if command_type in {"webcam.list", "webcam.snapshot"}:
                payload_result = self._webcam_request("list" if command_type == "webcam.list" else "snapshot", payload)
            elif command_type == "screen.screenshot":
                # Still captures hide pending approval windows only; the Agent main window stays visible.
                screenshot_payload = dict(payload)
                screenshot_payload["_hide_approval_windows"] = True
                payload_result = self.handlers.handle(command_type, screenshot_payload)
            else:
                payload_result = self.handlers.handle(command_type, payload)
            if command_type == "activity.start" and payload_result.get("status") in {"started", "already_running"}:
                with self.state_lock:
                    self.activity_active = True
            elif command_type == "activity.stop":
                with self.state_lock:
                    self.activity_active = False
            if command_type in {"activity.start", "activity.stop"}:
                self._notify_session_change()
            self._send(ws, result(command_id, agent_id, True, payload=payload_result))
        except Exception as exc:
            error = str(exc)
            self._report_command_error(command_type, error)
            self._send(
                ws,
                {
                    "type": "agent_command_error",
                    "agent_id": agent_id,
                    "command_id": command_id,
                    "command_type": command_type,
                    "error": error,
                },
            )
            self._send(ws, result(command_id, agent_id, False, error=error))


    def _notify_session_change(self) -> None:
        if not self.on_session_change:
            return
        try:
            self.on_session_change()
        except Exception:
            pass

    def reset_session_approvals(self) -> None:
        with self.state_lock:
            self.session_approvals.clear()

    def _approval_decision(self, message: dict) -> dict[str, Any]:
        command_type = message.get("command_type", "")
        family = self._approval_family(command_type)
        with self.state_lock:
            if family in self.session_approvals:
                return {"approved": True, "approval_mode": "session_cached", "policy_scope": "current_session"}
        raw = self.request_approval(message)
        if isinstance(raw, bool):
            return {"approved": raw, "approval_mode": "prompt_once", "policy_scope": "single_command"}
        approved = bool(raw.get("approved"))
        policy_scope = str(raw.get("policy_scope") or "single_command")
        if approved and policy_scope == "current_session":
            with self.state_lock:
                self.session_approvals.add(family)
        return {
            "approved": approved,
            "approval_mode": str(raw.get("approval_mode") or "prompt_once"),
            "policy_scope": policy_scope,
        }

    def _approval_family(self, command_type: str) -> str:
        # Session grants are intentionally command-specific: starting a stream never grants stopping it.
        return command_type

    def publish_agent_event(self, event_type: str, payload: dict[str, Any] | None = None) -> bool:
        with self.state_lock:
            ws = self.active_ws
            agent_id = self.config.agent_id
        if not ws or not agent_id:
            return False
        try:
            self._send(ws, {"type": event_type, "agent_id": agent_id, **(payload or {})})
            return True
        except Exception:
            return False

    def _report_command_error(self, command_type: str, error: str) -> None:
        if self.on_command_error:
            try:
                self.on_command_error(command_type, error)
            except Exception:
                pass

    def _stream_name(self, command_type: str) -> str:
        return "screen" if command_type.startswith("screen.") else "webcam"

    def _stream_active(self, command_type: str) -> bool:
        if self._stream_name(command_type) == "webcam":
            return self.webcam_stream is not None
        return "screen" in self.active_streams

    @property
    def webcam_active(self) -> bool:
        return self.webcam_stream is not None

    def _start_stream(self, ws, command_id: str, agent_id: str, command_type: str, payload: dict) -> None:
        stream = self._stream_name(command_type)
        if stream == "webcam":
            self._start_tauri_webcam(ws, command_id, agent_id, payload)
            return
        stop_stream = threading.Event()
        thread = threading.Thread(
            target=self._stream_frames,
            args=(ws, command_id, agent_id, stream, command_type, payload, stop_stream),
            daemon=True,
        )
        with self.state_lock:
            self.active_streams[stream] = (stop_stream, thread)
        self._notify_session_change()
        thread.start()

    def _stop_stream(self, command_type: str) -> dict:
        stream = self._stream_name(command_type)
        if stream == "webcam":
            return self._stop_tauri_webcam()
        with self.state_lock:
            active = self.active_streams.get(stream)
        if not active:
            return {"stream": stream, "status": "not_running"}
        stop_stream, thread = active
        stop_stream.set()
        if thread.is_alive():
            thread.join(timeout=2)
        with self.state_lock:
            self.active_streams.pop(stream, None)
        self._notify_session_change()
        return {"stream": stream, "status": "stop_requested"}

    def _stop_all_streams(self) -> None:
        if self.webcam_stream is not None:
            try:
                self._stop_tauri_webcam()
            except Exception:
                pass
        with self.state_lock:
            streams = list(self.active_streams.items())
        for stream, (stop_stream, thread) in streams:
            stop_stream.set()
            if thread.is_alive():
                thread.join(timeout=2)
            with self.state_lock:
                self.active_streams.pop(stream, None)
        self._notify_session_change()

    def _webcam_request(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.webcam_provider:
            raise RuntimeError("This Agent does not include the WebView2 webcam capture service. Install the latest desktop Agent.")
        response = self.webcam_provider(action, payload)
        if not isinstance(response, dict):
            raise RuntimeError("Webcam service returned an invalid response")
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        return response

    def _start_tauri_webcam(self, ws, command_id: str, agent_id: str, payload: dict[str, Any]) -> None:
        started = self._webcam_request("start", payload)
        fps = min(max(float(payload.get("fps", 15)), 1.0), 20.0)
        with self.state_lock:
            self.webcam_stream = {"ws": ws, "command_id": command_id, "agent_id": agent_id, "started": time.time(), "frames": 0, "fps": fps}
        self._notify_session_change()
        self._send(ws, {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": "webcam", "status": "running", "fps": fps, "backend": started.get("capture_backend", "webview2")})

    def publish_webcam_frame(self, frame: str, mime: str = "image/jpeg") -> dict[str, Any]:
        with self.state_lock:
            stream = self.webcam_stream
            if not stream:
                return {"accepted": False, "reason": "not_running"}
            stream["frames"] += 1
            frame_index = stream["frames"]
        self._send(stream["ws"], {"type": "stream_frame", "command_id": stream["command_id"], "agent_id": stream["agent_id"], "stream": "webcam", "mime": mime, "frame": frame, "frame_index": frame_index, "sent_at": time.time()})
        return {"accepted": True, "frame_index": frame_index}

    def fail_webcam_stream(self, error: str) -> None:
        with self.state_lock:
            stream = self.webcam_stream
            self.webcam_stream = None
        self._notify_session_change()
        if not stream:
            return
        self._report_command_error("webcam.live.start", error)
        self._send(stream["ws"], {"type": "stream_status", "command_id": stream["command_id"], "agent_id": stream["agent_id"], "stream": "webcam", "status": "failed", "fps": stream["fps"], "error": error})
        self._send(stream["ws"], result(stream["command_id"], stream["agent_id"], False, error=error))
    def _stop_tauri_webcam(self) -> dict[str, Any]:
        with self.state_lock:
            stream = self.webcam_stream
        if not stream:
            return {"stream": "webcam", "status": "not_running"}
        try:
            self._webcam_request("stop", {})
        finally:
            with self.state_lock:
                self.webcam_stream = None
            self._notify_session_change()
        self._send(stream["ws"], {"type": "stream_status", "command_id": stream["command_id"], "agent_id": stream["agent_id"], "stream": "webcam", "status": "stopped", "fps": stream["fps"]})
        self._send(stream["ws"], result(stream["command_id"], stream["agent_id"], True, payload={"stream": "webcam", "frames": stream["frames"], "duration_seconds": round(time.time() - stream["started"], 2), "fps": stream["fps"], "capture_backend": "webview2"}))
        return {"stream": "webcam", "status": "stopped"}
    def _stream_frames(self, ws, command_id: str, agent_id: str, stream: str, command_type: str, payload: dict, stop_stream: threading.Event) -> None:
        fps = min(max(float(payload.get("fps", 10)), 1.0), 15.0)
        frame_count = 0
        started = time.time()
        stream_payload = dict(payload)
        # Live screen sharing leaves the main Agent window visible by design.
        stream_payload["_hide_approval_windows"] = False
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
            error = str(exc)
            self._report_command_error(command_type, error)
            self._send(
                ws,
                {"type": "stream_status", "command_id": command_id, "agent_id": agent_id, "stream": stream, "status": "failed", "fps": fps, "error": error},
            )
            self._send(ws, result(command_id, agent_id, False, error=error))
        finally:
            with self.state_lock:
                self.active_streams.pop(stream, None)
            self._notify_session_change()

    def _notify_handler_provider(self, action: str) -> None:
        provider = getattr(self.handlers, "desktop_provider", None)
        if callable(provider):
            provider(action)

    def _send(self, ws, message: dict) -> None:
        with self.send_lock:
            ws.send(json.dumps(message))