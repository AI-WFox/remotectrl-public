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

