import json
from pathlib import Path

from remotectrl_agent.core.config import AgentConfig


def test_load_legacy_config_defaults_to_light_theme(monkeypatch, tmp_path: Path):
    import remotectrl_agent.core.config as config_module

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"agent_name": "Legacy Agent", "server_url": "https://example.test"}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_PATH", config_path)

    loaded = config_module.load_config()

    assert loaded.agent_name == "Legacy Agent"
    assert loaded.ui_theme == "light"

def test_new_agent_starts_without_allowed_folders():
    assert AgentConfig().allowed_folders == []