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
