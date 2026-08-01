from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "RemoteCtrlAgent"
CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class AgentConfig:
    server_url: str = "https://remotectrl-public-demo.onrender.com"
    agent_id: str | None = None
    agent_token: str | None = None
    agent_name: str = "RemoteCtrl Agent"
    allowed_folders: list[str] = field(default_factory=list)
    paused: bool = False
    dry_run_power: bool = True
    ui_theme: str = "light"
    privacy_defaults_version: int = 2


def load_config() -> AgentConfig:
    if not CONFIG_PATH.exists():
        return AgentConfig()
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))

    # Legacy configs predate the consent-first file whitelist. Treat their
    # permissions as untrusted and require the local user to grant folders again.
    migrate_privacy_defaults = "privacy_defaults_version" not in data
    if migrate_privacy_defaults:
        data["allowed_folders"] = []
        data["privacy_defaults_version"] = AgentConfig.privacy_defaults_version

    config = AgentConfig(**data)
    if migrate_privacy_defaults:
        save_config(config)
    return config


def save_config(config: AgentConfig) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")