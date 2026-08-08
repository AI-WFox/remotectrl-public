from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_DIR = Path(os.getenv("APPDATA", Path.home())) / "RemoteCtrlAgent"
CONFIG_PATH = APP_DIR / "config.json"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect_agent_token(token: str) -> str:
    if os.name != "nt":
        return "portable:" + base64.b64encode(token.encode()).decode()
    source, source_buffer = _blob(token.encode())
    target = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(target.pbData, target.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)
        del source_buffer


def unprotect_agent_token(value: str) -> str:
    if value.startswith("portable:"):
        return base64.b64decode(value.removeprefix("portable:")).decode()
    if not value.startswith("dpapi:") or os.name != "nt":
        raise ValueError("Agent credential cannot be decrypted on this Windows account")
    encrypted = base64.b64decode(value.removeprefix("dpapi:"))
    source, source_buffer = _blob(encrypted)
    target = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)
        del source_buffer


@dataclass
class AgentConfig:
    server_url: str = "https://remotectrl-public-demo.onrender.com"
    agent_id: str | None = None
    agent_token: str | None = field(default=None, repr=False)
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

    migrate_privacy_defaults = "privacy_defaults_version" not in data
    if migrate_privacy_defaults:
        data["allowed_folders"] = []
        data["privacy_defaults_version"] = AgentConfig.privacy_defaults_version

    protected_token = data.pop("agent_token_protected", None)
    legacy_raw_token = data.get("agent_token")
    migrate_portable_token = (
        os.name == "nt" and isinstance(protected_token, str) and protected_token.startswith("portable:")
    )
    if protected_token:
        data["agent_token"] = unprotect_agent_token(str(protected_token))

    config = AgentConfig(**data)
    if migrate_privacy_defaults or legacy_raw_token or migrate_portable_token:
        save_config(config)
    return config


def save_config(config: AgentConfig) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    token = data.pop("agent_token", None)
    if token:
        data["agent_token_protected"] = protect_agent_token(token)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")