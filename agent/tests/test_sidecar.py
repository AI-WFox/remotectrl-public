from __future__ import annotations

from pathlib import Path

from remotectrl_agent.core.config import AgentConfig
from remotectrl_agent.sidecar import AgentSidecar


class FakeBridge:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def event(self, name: str, data: dict | None = None) -> None:
        self.events.append((name, data or {}))

    def request_ui(self, _method: str, _params: dict | None = None, timeout: float = 90.0):
        return {"approved": True, "approval_mode": "prompt_once", "policy_scope": "single_command"}


def test_sidecar_state_never_exposes_agent_token():
    app = AgentSidecar(FakeBridge(), AgentConfig(agent_id="agent-1", agent_token="secret-token"))

    state = app.state()

    assert state["config"]["enrolled"] is True
    assert "agent_token" not in state["config"]


def test_sidecar_folder_and_power_updates_are_persisted(monkeypatch, tmp_path: Path):
    import remotectrl_agent.sidecar as sidecar_module

    monkeypatch.setattr(sidecar_module, "save_config", lambda _config: None)
    config = AgentConfig(allowed_folders=[])
    app = AgentSidecar(FakeBridge(), config)
    folder = tmp_path / "allowed"
    folder.mkdir()

    app.dispatch("agent.add_allowed_folder", {"path": str(folder)})
    app.dispatch("agent.power_mode", {"enabled": True})

    assert config.allowed_folders == [str(folder)]
    assert config.dry_run_power is False

def test_sidecar_keeps_saved_enrollment_offline_until_local_connect(monkeypatch):
    config = AgentConfig(agent_id="agent-1", agent_token="saved-token", paused=False)
    app = AgentSidecar(FakeBridge(), config)
    started = []
    monkeypatch.setattr(app.client, "start", lambda: started.append(True))

    app.start_saved_connection()

    assert started == []


def test_load_config_accepts_windows_utf8_bom(monkeypatch, tmp_path: Path):
    import remotectrl_agent.core.config as config_module

    config_path = tmp_path / "config.json"
    config_path.write_text('{"agent_name":"BOM Agent"}', encoding="utf-8-sig")
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    assert config_module.load_config().agent_name == "BOM Agent"


def test_sidecar_sends_activity_detail_to_gateway_not_agent_console(monkeypatch):
    bridge = FakeBridge()
    app = AgentSidecar(bridge, AgentConfig(agent_id="agent-1", agent_token="secret-token"))
    relayed: list[dict] = []
    monkeypatch.setattr(app.client, "publish_activity_event", lambda event: relayed.append(event) or True)

    event = {"time": "2026-07-26T20:00:00", "type": "keyboard.shortcut", "detail": {"keys": "Ctrl + S"}}
    app._activity_event("activity.event", event)

    assert relayed == [event]
    assert ("activity.event", event) not in bridge.events

def test_session_state_event_reflects_running_webcam():
    bridge = FakeBridge()
    app = AgentSidecar(bridge, AgentConfig(agent_id="agent-1", agent_token="secret-token"))
    app.client.webcam_stream = {"command_id": "stream-1"}

    app._on_session_change()

    name, payload = bridge.events[-1]
    assert name == "agent.session_state"
    assert payload["state"]["sessions"]["webcam"] is True

def test_local_disconnect_stops_gateway_connection_and_persists_pause(monkeypatch):
    import remotectrl_agent.sidecar as sidecar_module

    monkeypatch.setattr(sidecar_module, "save_config", lambda _config: None)
    config = AgentConfig(agent_id="agent-1", agent_token="saved-token", paused=False)
    app = AgentSidecar(FakeBridge(), config)
    stopped: list[bool] = []
    monkeypatch.setattr(app.client, "stop", lambda: stopped.append(True))

    state = app.dispatch("agent.disconnect", {})

    assert stopped == [True]
    assert config.paused is True
    assert state["status"] == "Disconnected by local user"
def test_local_activity_stop_publishes_idle_session_state(monkeypatch):
    import remotectrl_agent.sidecar as sidecar_module

    monkeypatch.setattr(sidecar_module, "save_config", lambda _config: None)
    bridge = FakeBridge()
    app = AgentSidecar(bridge, AgentConfig(agent_id="agent-1", agent_token="saved-token"))
    gateway_events: list[tuple[str, dict]] = []
    monkeypatch.setattr(app.activity, "stop", lambda: "stopped")
    monkeypatch.setattr(app.client, "publish_agent_event", lambda event_type, payload=None: gateway_events.append((event_type, payload or {})) or True)

    app.dispatch("agent.activity_stop_local", {})

    assert ("agent_session_state", {"sessions": {"screen": False, "webcam": False, "keycapture": False, "activity": False}, "source": "local"}) in gateway_events