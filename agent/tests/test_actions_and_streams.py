import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.modules.setdefault("requests", SimpleNamespace())
sys.modules.setdefault("websocket", SimpleNamespace(WebSocketTimeoutException=Exception))

from remotectrl_agent.core.client import AgentClient
from remotectrl_agent.core.config import AgentConfig
from remotectrl_agent.core.handlers import CommandHandlers


class FakeWs:
    def __init__(self):
        self.messages = []

    def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


def test_app_start_supports_safe_preset(monkeypatch, tmp_path: Path):
    fake_exe = tmp_path / "notepad.exe"
    fake_exe.write_text("", encoding="utf-8")
    launched = []

    import remotectrl_agent.core.handlers as handlers_module

    monkeypatch.setitem(handlers_module.APP_PRESETS, "notepad", [str(fake_exe)])
    monkeypatch.setattr(handlers_module.subprocess, "Popen", lambda args, close_fds=True: launched.append(args))

    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    result = handlers.app_start({"preset": "notepad"})

    assert result["status"] == "started"
    assert result["path"] == str(fake_exe)
    assert launched == [[str(fake_exe)]]


def test_protected_process_kill_is_blocked(monkeypatch):
    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "lsass.exe"

        def terminate(self):
            raise AssertionError("protected process must not be terminated")

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=FakeProcess))
    handlers = CommandHandlers(AgentConfig(), lambda action: "")

    with pytest.raises(PermissionError):
        handlers.process_kill({"pid": 44})


def test_power_defaults_to_dry_run():
    handlers = CommandHandlers(AgentConfig(dry_run_power=True), lambda action: "")

    result = handlers.power_shutdown({})

    assert result["status"] == "dry_run"
    assert result["action"] == "shutdown"


def test_stream_start_and_stop_sends_status_and_result():
    class FakeHandlers:
        def handle(self, command_type, payload):
            assert command_type == "screen.screenshot"
            return {"mime": "image/jpeg", "image": "ZmFrZQ=="}

    ws = FakeWs()
    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, lambda command: True)

    client._start_stream(ws, "cmd-start", "agent-1", "screen.live.start", {"fps": 10, "quality": 65})
    time.sleep(0.15)
    result = client._stop_stream("screen.live.stop")
    time.sleep(0.05)

    assert result["status"] == "stop_requested"
    assert any(message["type"] == "stream_status" and message["status"] == "running" for message in ws.messages)
    assert any(message["type"] == "stream_frame" for message in ws.messages)
    assert any(message["type"] == "stream_status" and message["status"] == "stopped" for message in ws.messages)
    assert any(message["type"] == "command_result" and message["ok"] is True for message in ws.messages)


def test_duplicate_stream_start_returns_already_running_without_approval():
    class FakeHandlers:
        def handle(self, command_type, payload):
            return {"mime": "image/jpeg", "image": "ZmFrZQ=="}

    approval_calls = []
    ws = FakeWs()
    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, lambda command: approval_calls.append(command) or True)
    client._start_stream(ws, "cmd-start", "agent-1", "screen.live.start", {"fps": 10})
    time.sleep(0.05)

    client._handle_message(
        ws,
        {
            "type": "command",
            "command_id": "cmd-duplicate",
            "agent_id": "agent-1",
            "command_type": "screen.live.start",
            "payload": {"fps": 10},
            "requires_approval": True,
        },
    )
    client._stop_stream("screen.live.stop")

    duplicate = [message for message in ws.messages if message.get("command_id") == "cmd-duplicate"]
    assert approval_calls == []
    assert duplicate[-1]["type"] == "command_result"
    assert duplicate[-1]["payload"]["status"] == "already_running"


def test_session_cached_approval_sends_metadata_and_skips_second_prompt():
    class FakeHandlers:
        def handle(self, command_type, payload):
            return {"status": "ok", "command_type": command_type}

    approval_calls = []

    def approve_for_session(command):
        approval_calls.append(command)
        return {"approved": True, "approval_mode": "prompt_once", "policy_scope": "current_session"}

    ws = FakeWs()
    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, approve_for_session)
    for command_id in ["cmd-1", "cmd-2"]:
        client._handle_message(
            ws,
            {
                "type": "command",
                "command_id": command_id,
                "agent_id": "agent-1",
                "command_type": "process.list",
                "payload": {},
                "requires_approval": True,
            },
        )

    approvals = [message for message in ws.messages if message["type"] == "approval_response"]
    assert len(approval_calls) == 1
    assert approvals[0]["approval_mode"] == "prompt_once"
    assert approvals[0]["policy_scope"] == "current_session"
    assert approvals[1]["approval_mode"] == "session_cached"
    assert approvals[1]["policy_scope"] == "current_session"


def test_stop_stream_does_not_require_approval_when_backend_marks_safe():
    class FakeHandlers:
        def handle(self, command_type, payload):
            return {"mime": "image/jpeg", "image": "ZmFrZQ=="}

    approval_calls = []
    ws = FakeWs()
    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, lambda command: approval_calls.append(command) or True)
    client._start_stream(ws, "cmd-start", "agent-1", "screen.live.start", {"fps": 10})
    time.sleep(0.05)
    client._handle_message(
        ws,
        {
            "type": "command",
            "command_id": "cmd-stop",
            "agent_id": "agent-1",
            "command_type": "screen.live.stop",
            "payload": {},
            "requires_approval": False,
        },
    )

    stop_result = [message for message in ws.messages if message.get("command_id") == "cmd-stop"][-1]
    assert approval_calls == []
    assert stop_result["type"] == "command_result"
    assert stop_result["payload"]["status"] in {"stop_requested", "not_running"}
