from pathlib import Path

import pytest

from remotectrl_agent.core.config import AgentConfig
from remotectrl_agent.core.handlers import CommandHandlers


def test_files_are_limited_to_allowed_roots(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    config = AgentConfig(allowed_folders=[str(allowed)])
    handlers = CommandHandlers(config, lambda action: "")
    assert handlers.files_list({"path": str(allowed)})["path"] == str(allowed.resolve())
    with pytest.raises(PermissionError):
        handlers.files_list({"path": str(tmp_path.parent)})



def test_files_list_hides_system_and_temporary_entries(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "visible.txt").write_text("ok", encoding="utf-8")
    (allowed / "desktop.ini").write_text("hidden", encoding="utf-8")
    (allowed / "~$draft.docx").write_text("lock", encoding="utf-8")
    config = AgentConfig(allowed_folders=[str(allowed)])
    handlers = CommandHandlers(config, lambda action: "")

    result = handlers.files_list({"path": str(allowed)})

    assert result["allowed_root"] == str(allowed.resolve())
    assert [entry["name"] for entry in result["entries"]] == ["visible.txt"]
    assert result["hidden_filtered"] == 2
