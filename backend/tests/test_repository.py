from pathlib import Path

from app.core.db import init_db
from app.services.repository import Repository


def test_command_sensitive_approval(tmp_path: Path):
    db = tmp_path / "test.db"
    init_db(db)
    repo = Repository(db)
    repo.ensure_admin("a@b.test", "pw")
    record, token = repo.create_enrollment_token("demo", False)
    enrolled = repo.enroll_agent(token, "Demo", "host", "Windows", "127.0.0.1")
    assert enrolled is not None
    agent, _agent_token = enrolled
    command = repo.create_command(agent["id"], "screen.screenshot", {}, "a@b.test")
    assert command["requires_approval"] is True
    command = repo.create_command(agent["id"], "process.list", {}, "a@b.test")
    assert command["requires_approval"] is True
    command = repo.create_command(agent["id"], "screen.live.stop", {}, "a@b.test")
    assert command["requires_approval"] is False
