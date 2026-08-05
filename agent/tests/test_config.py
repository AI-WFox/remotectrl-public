import json
from pathlib import Path

from remotectrl_agent.core.config import AgentConfig, save_config


def test_load_legacy_config_defaults_to_light_theme(monkeypatch, tmp_path: Path):
    import remotectrl_agent.core.config as config_module

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agent_name": "Legacy Agent",
                "server_url": "https://example.test",
                "allowed_folders": [r"C:\Users\legacy\Documents"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    loaded = config_module.load_config()

    assert loaded.agent_name == "Legacy Agent"
    assert loaded.ui_theme == "light"
    assert loaded.allowed_folders == []
    assert loaded.privacy_defaults_version == 2

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["allowed_folders"] == []
    assert persisted["privacy_defaults_version"] == 2


def test_current_config_preserves_allowed_folders(monkeypatch, tmp_path: Path):
    import remotectrl_agent.core.config as config_module

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "privacy_defaults_version": 2,
                "allowed_folders": [r"D:\Approved"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    loaded = config_module.load_config()

    assert loaded.allowed_folders == [r"D:\Approved"]


def test_new_agent_starts_without_allowed_folders():
    assert AgentConfig().allowed_folders == []

def test_agent_token_is_saved_protected_and_legacy_raw_token_is_migrated(monkeypatch, tmp_path: Path):
    import remotectrl_agent.core.config as config_module

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(config_module, "protect_agent_token", lambda token: f"dpapi:{token}-protected")
    monkeypatch.setattr(config_module, "unprotect_agent_token", lambda value: value.removeprefix("dpapi:").removesuffix("-protected"))

    save_config(AgentConfig(agent_id="agent-1", agent_token="secret-token"))
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert "agent_token" not in persisted
    assert persisted["agent_token_protected"] == "dpapi:secret-token-protected"
    assert config_module.load_config().agent_token == "secret-token"

    persisted["agent_token"] = "legacy-token"
    persisted.pop("agent_token_protected")
    config_path.write_text(json.dumps(persisted), encoding="utf-8")
    assert config_module.load_config().agent_token == "legacy-token"
    migrated = json.loads(config_path.read_text(encoding="utf-8"))
    assert "agent_token" not in migrated
    assert migrated["agent_token_protected"] == "dpapi:legacy-token-protected"