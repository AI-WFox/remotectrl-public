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
    result = handlers.app_start({"preset": "notepad", "mode": "new_instance"})

    assert result["status"] == "started_new"
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


def test_process_kill_returns_already_stopped_when_pid_disappeared(monkeypatch):
    class NoSuchProcess(Exception):
        pass

    class MissingProcess:
        def __init__(self, _pid):
            raise NoSuchProcess()

    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Process=MissingProcess, NoSuchProcess=NoSuchProcess, AccessDenied=PermissionError, TimeoutExpired=TimeoutError),
    )
    handlers = CommandHandlers(AgentConfig(), lambda action: "")

    result = handlers.process_kill({"pid": 44})

    assert result == {"pid": 44, "name": "unknown", "status": "already_stopped"}


def test_process_kill_fails_when_process_does_not_exit(monkeypatch):
    class TimeoutExpired(Exception):
        pass

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "demo.exe"

        def terminate(self):
            return None

        def wait(self, timeout):
            raise TimeoutExpired(timeout)

    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Process=FakeProcess, NoSuchProcess=LookupError, AccessDenied=PermissionError, TimeoutExpired=TimeoutExpired),
    )
    handlers = CommandHandlers(AgentConfig(), lambda action: "")

    with pytest.raises(RuntimeError, match="did not stop within 2 seconds"):
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


def test_stop_stream_requires_approval_when_backend_marks_sensitive():
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
            "requires_approval": True,
        },
    )

    stop_result = [message for message in ws.messages if message.get("command_id") == "cmd-stop"][-1]
    assert len(approval_calls) == 1
    assert stop_result["type"] == "command_result"
    assert stop_result["payload"]["status"] in {"stop_requested", "not_running"}


def test_app_start_focuses_existing_window_without_launch(monkeypatch, tmp_path: Path):
    fake_exe = tmp_path / "notepad.exe"
    fake_exe.write_text("", encoding="utf-8")
    launched = []

    import remotectrl_agent.core.handlers as handlers_module

    monkeypatch.setitem(handlers_module.APP_PRESETS, "notepad", [str(fake_exe)])
    monkeypatch.setattr(handlers_module.subprocess, "Popen", lambda args, close_fds=True: launched.append(args))
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    monkeypatch.setattr(handlers, "_visible_windows", lambda: [{"pid": 10, "name": "notepad.exe", "title": "Untitled - Notepad", "hwnd": 123}])
    monkeypatch.setattr(handlers, "_focus_window", lambda hwnd: True)

    result = handlers.app_start({"preset": "notepad", "mode": "focus_existing"})

    assert result["status"] == "focused_existing"
    assert launched == []


def test_app_start_does_not_confuse_untitled_paint_with_notepad(monkeypatch, tmp_path: Path):
    fake_exe = tmp_path / "notepad.exe"
    fake_exe.write_text("", encoding="utf-8")
    launched = []

    import remotectrl_agent.core.handlers as handlers_module

    monkeypatch.setitem(handlers_module.APP_PRESETS, "notepad", [str(fake_exe)])
    monkeypatch.setattr(handlers_module.subprocess, "Popen", lambda args, close_fds=True: launched.append(args))
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    monkeypatch.setattr(
        handlers,
        "_visible_windows",
        lambda: [{"pid": 10, "name": "mspaint.exe", "title": "Untitled - Paint", "hwnd": 123}],
    )
    monkeypatch.setattr(handlers, "_focus_window", lambda _hwnd: pytest.fail("Paint must not be focused for Notepad"))

    result = handlers.app_start({"preset": "notepad", "mode": "focus_existing"})

    assert result["status"] == "fallback_started"
    assert launched == [[str(fake_exe)]]

def test_app_start_focuses_uwp_hosted_window_by_title(monkeypatch, tmp_path: Path):
    fake_exe = tmp_path / "calc.exe"
    fake_exe.write_text("", encoding="utf-8")
    launched = []

    import remotectrl_agent.core.handlers as handlers_module

    monkeypatch.setitem(handlers_module.APP_PRESETS, "calculator", [str(fake_exe)])
    monkeypatch.setattr(handlers_module.subprocess, "Popen", lambda args, close_fds=True: launched.append(args))
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    monkeypatch.setattr(
        handlers,
        "_visible_windows",
        lambda: [{"pid": 10, "name": "ApplicationFrameHost.exe", "title": "Calculator", "hwnd": 123}],
    )
    monkeypatch.setattr(handlers, "_focus_window", lambda hwnd: True)

    result = handlers.app_start({"preset": "calculator", "mode": "focus_existing"})

    assert result["status"] == "focused_existing"
    assert result["window"]["title"] == "Calculator"
    assert launched == []


def test_app_start_focuses_modern_calculator_process_alias(monkeypatch, tmp_path: Path):
    fake_exe = tmp_path / "calc.exe"
    fake_exe.write_text("", encoding="utf-8")
    launched = []

    import remotectrl_agent.core.handlers as handlers_module

    monkeypatch.setitem(handlers_module.APP_PRESETS, "calculator", [str(fake_exe)])
    monkeypatch.setattr(handlers_module.subprocess, "Popen", lambda args, close_fds=True: launched.append(args))
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    monkeypatch.setattr(
        handlers,
        "_visible_windows",
        lambda: [{"pid": 10, "name": "CalculatorApp.exe", "title": "Calculator", "hwnd": 123}],
    )
    monkeypatch.setattr(handlers, "_focus_window", lambda hwnd: True)

    result = handlers.app_start({"preset": "calculator", "mode": "focus_existing"})

    assert result["status"] == "focused_existing"
    assert result["window"]["name"] == "CalculatorApp.exe"
    assert launched == []


def test_app_list_groups_modern_calculator_process_alias():
    handlers = CommandHandlers(AgentConfig(), lambda action: "")

    result = handlers._group_visible_apps(
        [{"pid": 10, "name": "CalculatorApp.exe", "title": "Calculator", "hwnd": 123}]
    )

    assert result == [
        {
            "app_key": "calculator",
            "name": "Calculator",
            "window_count": 1,
            "process_names": ["CalculatorApp.exe"],
        }
    ]

def test_process_list_includes_visible_apps(monkeypatch):
    class FakeProc:
        info = {"pid": 11, "name": "python.exe", "status": "running", "cpu_percent": 0.0, "memory_info": SimpleNamespace(rss=1048576)}

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=lambda attrs: [FakeProc()]))
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    monkeypatch.setattr(handlers, "app_list", lambda payload: {"items": [{"app_key": "code", "name": "Code", "window_count": 1, "process_names": ["Code.exe"]}], "count": 1})

    result = handlers.process_list({})

    assert result["app_count"] == 1
    assert result["apps"][0]["app_key"] == "code"
    assert result["items"][0]["name"] == "python.exe"


def test_files_roots_and_empty_files_list_do_not_open_default_folder(tmp_path: Path):
    allowed = tmp_path / "Allowed"
    allowed.mkdir()
    handlers = CommandHandlers(AgentConfig(allowed_folders=[str(allowed)]), lambda action: "")

    roots = handlers.files_roots({})
    listed = handlers.files_list({})

    assert roots["requires_selection"] is True
    assert roots["roots"][0]["path"] == str(allowed)
    assert listed["requires_selection"] is True
    assert listed["entries"] == []



def test_session_cached_approval_does_not_cover_different_session_action():
    class FakeHandlers:
        def handle(self, command_type, payload):
            return {"status": "ok"}

    approval_calls = []

    def approve_for_session(command):
        approval_calls.append(command["command_type"])
        return {"approved": True, "approval_mode": "prompt_once", "policy_scope": "current_session"}

    ws = FakeWs()
    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, approve_for_session)
    client._handle_message(
        ws,
        {
            "type": "command",
            "command_id": "start-1",
            "agent_id": "agent-1",
            "command_type": "activity.start",
            "payload": {},
            "requires_approval": True,
        },
    )
    client._handle_message(
        ws,
        {
            "type": "command",
            "command_id": "stop-1",
            "agent_id": "agent-1",
            "command_type": "activity.stop",
            "payload": {},
            "requires_approval": True,
        },
    )

    approvals = [message for message in ws.messages if message["type"] == "approval_response"]
    assert approval_calls == ["activity.start", "activity.stop"]
    assert approvals[-1]["approval_mode"] == "prompt_once"
    assert approvals[-1]["policy_scope"] == "current_session"


def test_session_cached_approval_does_not_cross_sensitive_actions():
    class FakeHandlers:
        def handle(self, command_type, payload):
            return {"status": "ok"}

    approval_calls = []

    def approve_for_session(command):
        approval_calls.append(command["command_type"])
        return {"approved": True, "approval_mode": "prompt_once", "policy_scope": "current_session"}

    ws = FakeWs()
    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, approve_for_session)
    for command_id, command_type in [("list-1", "process.list"), ("kill-1", "process.kill")]:
        client._handle_message(
            ws,
            {
                "type": "command",
                "command_id": command_id,
                "agent_id": "agent-1",
                "command_type": command_type,
                "payload": {},
                "requires_approval": True,
            },
        )

    approvals = [message for message in ws.messages if message["type"] == "approval_response"]
    assert approval_calls == ["process.list", "process.kill"]
    assert approvals[-1]["approval_mode"] == "prompt_once"
    assert approvals[-1]["policy_scope"] == "current_session"


def test_power_status_and_sleep_dry_run():
    handlers = CommandHandlers(AgentConfig(dry_run_power=True), lambda action: "")

    status = handlers.power_status({})
    sleep = handlers.power_sleep({})

    assert status["action"] == "status"
    assert status["dry_run_power"] is True
    assert "sleep" in status["supported_actions"]
    assert "cpu_percent" in status
    assert "system_uptime_seconds" in status
    assert sleep["action"] == "sleep"
    assert sleep["status"] == "dry_run"


def test_power_sleep_real_mode_uses_windows_command(monkeypatch):
    launched = []
    monkeypatch.setattr("remotectrl_agent.core.handlers.subprocess.Popen", lambda args: launched.append(args))
    handlers = CommandHandlers(AgentConfig(dry_run_power=False), lambda action: "")

    result = handlers.power_sleep({})

    assert result["status"] == "requested"
    assert launched == [["rundll32.exe", "powrprof.dll,SetSuspendState", "0,0,0"]]


def test_agent_windows_are_excluded_from_visible_app_results():
    handlers = CommandHandlers(AgentConfig(), lambda action: "")

    assert handlers._is_agent_window("RemoteCtrlAgent.exe", "RemoteCtrl Agent") is True
    assert handlers._is_agent_window("python.exe", "RemoteCtrl Approval") is True
    assert handlers._is_agent_window("Code.exe", "RemoteCtrl Source File") is False
    assert handlers._is_agent_window("notepad.exe", "Untitled - Notepad") is False

def test_webview2_webcam_forwards_frames():
    calls = []

    def camera_provider(action, payload):
        calls.append((action, payload))
        return {"capture_backend": "webview2", "status": "running"}

    client = AgentClient(AgentConfig(), SimpleNamespace(), lambda _status: None, lambda _message: False, camera_provider)
    ws = FakeWs()

    client._start_tauri_webcam(ws, "start-command", "agent-1", {"fps": 12})
    accepted = client.publish_webcam_frame("ZmFrZS1mcmFtZQ==")
    stopped = client._stop_tauri_webcam()

    assert accepted == {"accepted": True, "frame_index": 1}
    assert stopped == {"stream": "webcam", "status": "stopped"}
    assert [action for action, _payload in calls] == ["start", "stop"]
    assert [message["type"] for message in ws.messages] == ["stream_status", "stream_frame", "stream_status", "command_result"]
    assert ws.messages[0]["status"] == "running"
    assert ws.messages[1]["frame"] == "ZmFrZS1mcmFtZQ=="
    assert ws.messages[-1]["ok"] is True

def test_removed_webcam_snapshot_does_not_call_camera_provider():
    calls = []

    def camera_provider(action, payload):
        calls.append((action, payload))
        return {"capture_backend": "webview2"}

    class RejectingHandlers:
        @staticmethod
        def handle(command_type, payload):
            raise ValueError(f"Unsupported command: {command_type}")

    client = AgentClient(AgentConfig(), RejectingHandlers(), lambda _status: None, lambda _message: False, camera_provider)
    ws = FakeWs()

    client._handle_message(
        ws,
        {
            "type": "command",
            "command_id": "snapshot-command",
            "agent_id": "agent-1",
            "command_type": "webcam.snapshot",
            "payload": {"quality": 85},
            "requires_approval": False,
        },
    )

    assert calls == []
    assert ws.messages[-1]["type"] == "command_result"
    assert ws.messages[-1]["ok"] is False
    assert "Unsupported command: webcam.snapshot" in ws.messages[-1]["error"]


def test_local_webcam_stop_skips_reentrant_camera_request():
    calls = []

    def camera_provider(action, payload):
        calls.append((action, payload))
        return {"capture_backend": "webview2", "status": "running"}

    client = AgentClient(AgentConfig(), SimpleNamespace(), lambda _status: None, lambda _message: False, camera_provider)
    ws = FakeWs()
    client._start_tauri_webcam(ws, "start-command", "agent-1", {"fps": 12})

    stopped = client.stop_stream_local("webcam", local_capture_stopped=True)

    assert stopped == {"stream": "webcam", "status": "stopped"}
    assert client.webcam_stream is None
    assert [action for action, _payload in calls] == ["start"]
    assert ws.messages[-2]["status"] == "stopped"
    assert ws.messages[-1]["ok"] is True

def test_capture_still_hides_only_pending_approval_windows():
    class FakeHandlers:
        def __init__(self):
            self.payloads = []

        def handle(self, command_type, payload):
            self.payloads.append((command_type, payload))
            return {"mime": "image/jpeg", "image": "ZmFrZQ=="}

    handlers = FakeHandlers()
    ws = FakeWs()
    client = AgentClient(AgentConfig(), handlers, lambda status: None, lambda command: True)
    client.active_streams["screen"] = (object(), object())

    client._handle_message(
        ws,
        {
            "type": "command",
            "command_id": "still-1",
            "agent_id": "agent-1",
            "command_type": "screen.screenshot",
            "payload": {"quality": 85},
            "requires_approval": False,
        },
    )

    assert handlers.payloads == [("screen.screenshot", {"quality": 85, "_hide_approval_windows": True})]
    assert ws.messages[-1]["type"] == "command_result"
    assert ws.messages[-1]["ok"] is True

def test_session_approval_is_scoped_to_application_resource():
    class FakeHandlers:
        def handle(self, command_type, payload):
            return {"status": "ok"}

    prompts = []

    def approve(command):
        prompts.append(command)
        return {"approved": True, "approval_mode": "prompt_once", "policy_scope": "current_session"}

    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, approve)
    notepad = {"command_type": "app.start", "payload": {"preset": "notepad", "mode": "focus_existing"}}
    chrome = {"command_type": "app.start", "payload": {"preset": "chrome", "mode": "focus_existing"}}

    assert client._approval_decision(notepad)["approval_mode"] == "prompt_once"
    assert client._approval_decision(chrome)["approval_mode"] == "prompt_once"
    assert client._approval_decision(notepad)["approval_mode"] == "session_cached"
    assert len(prompts) == 2


def test_session_approval_is_scoped_to_file_path():
    class FakeHandlers:
        def handle(self, command_type, payload):
            return {"status": "ok"}

    prompts = []

    def approve(command):
        prompts.append(command)
        return {"approved": True, "approval_mode": "prompt_once", "policy_scope": "current_session"}

    client = AgentClient(AgentConfig(), FakeHandlers(), lambda status: None, approve)
    first = {"command_type": "files.list", "payload": {"path": "C:\\Allowed\\One"}}
    second = {"command_type": "files.list", "payload": {"path": "D:\\Allowed\\Two"}}

    client._approval_decision(first)
    client._approval_decision(second)
    assert client._approval_decision(first)["approval_mode"] == "session_cached"
    assert len(prompts) == 2


def test_app_list_groups_visible_windows_by_logical_application(monkeypatch):
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    monkeypatch.setattr(
        handlers,
        "_visible_windows",
        lambda: [
            {"pid": 10, "name": "chrome.exe", "title": "First tab", "hwnd": 101},
            {"pid": 20, "name": "chrome.exe", "title": "Second tab", "hwnd": 102},
            {"pid": 30, "name": "notepad.exe", "title": "Notes", "hwnd": 103},
        ],
    )

    result = handlers.app_list({})

    assert result["count"] == 2
    assert result["window_count"] == 3
    chrome = next(item for item in result["items"] if item["app_key"] == "chrome")
    assert chrome["name"] == "Chrome"
    assert chrome["window_count"] == 2
    assert "pid" not in chrome
    assert "title" not in chrome

def test_app_stop_closes_every_visible_window_for_logical_app(monkeypatch):
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    snapshots = iter([
        [
            {"pid": 10, "name": "chrome.exe", "title": "First tab", "hwnd": 101},
            {"pid": 20, "name": "chrome.exe", "title": "Second tab", "hwnd": 102},
            {"pid": 30, "name": "notepad.exe", "title": "Notes", "hwnd": 103},
        ],
        [],
    ])
    monkeypatch.setattr(handlers, "_visible_windows", lambda: next(snapshots))
    import remotectrl_agent.core.handlers as handlers_module
    monkeypatch.setattr(handlers_module.time, "sleep", lambda _seconds: None)

    result = handlers.app_stop({"app_key": "chrome"})

    assert result["app_key"] == "chrome"
    assert result["status"] == "stopped"
    assert result["process_names"] == ["chrome.exe"]
    assert result["remaining_windows_before_terminate"] == 0

def test_app_stop_terminate_fallback_returns_json_serializable_result(monkeypatch):
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    windows = [{"pid": 10, "name": "chrome.exe", "title": "Chrome", "hwnd": 0}]
    monkeypatch.setattr(handlers, "_visible_windows", lambda: windows)
    remaining = iter([windows, []])
    monkeypatch.setattr(handlers, "_wait_for_app_windows_to_close", lambda _key, timeout: next(remaining))

    terminated = []
    process = SimpleNamespace(info={"pid": 10, "name": "chrome.exe"}, terminate=lambda: terminated.append(10))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=lambda _attrs: [process]))

    result = handlers.app_stop({"app_key": "chrome"})

    assert terminated == [10]
    assert result["status"] == "stopped"
    assert result["remaining_windows"] == 0
    assert result["process_names"] == ["chrome.exe"]
    assert json.loads(json.dumps(result))["terminated_processes"] == 1


def test_app_stop_fails_when_visible_windows_remain(monkeypatch):
    handlers = CommandHandlers(AgentConfig(), lambda action: "")
    windows = [{"pid": 10, "name": "chrome.exe", "title": "Chrome", "hwnd": 0}]
    monkeypatch.setattr(handlers, "_visible_windows", lambda: windows)
    monkeypatch.setattr(handlers, "_wait_for_app_windows_to_close", lambda _key, timeout: windows)
    process = SimpleNamespace(info={"pid": 10, "name": "chrome.exe"}, terminate=lambda: None)
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(process_iter=lambda _attrs: [process]))

    with pytest.raises(RuntimeError, match="still has 1 visible window"):
        handlers.app_stop({"app_key": "chrome"})


def test_process_kill_waits_for_process_exit(monkeypatch):
    waited = []

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def name(self):
            return "demo.exe"

        def terminate(self):
            return None

        def wait(self, timeout):
            waited.append(timeout)
            return 0

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=FakeProcess, TimeoutExpired=TimeoutError))
    handlers = CommandHandlers(AgentConfig(), lambda action: "")

    result = handlers.process_kill({"pid": 44})

    assert result["status"] == "stopped"
    assert waited == [2]
