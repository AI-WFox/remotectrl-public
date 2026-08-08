from pathlib import Path

from app.core.db import init_db
from app.core.security import verify_password
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
    assert command["requires_approval"] is True
    command = repo.create_command(agent["id"], "files.roots", {}, "a@b.test")
    assert command["requires_approval"] is True
    command = repo.create_command(agent["id"], "activity.stop", {}, "a@b.test")
    assert command["requires_approval"] is True
    command = repo.create_command(agent["id"], "power.sleep", {}, "a@b.test")
    assert command["requires_approval"] is True
    command = repo.create_command(agent["id"], "power.status", {}, "a@b.test")
    assert command["requires_approval"] is True


def test_ensure_admin_updates_bootstrap_password(tmp_path: Path):
    db = tmp_path / "admin-password.db"
    init_db(db)
    repo = Repository(db)

    repo.ensure_admin("admin@example.test", "first-password")
    repo.ensure_admin("admin@example.test", "second-password")

    user = repo.find_user_by_email("admin@example.test")
    assert user is not None
    assert verify_password("second-password", user["password_hash"])
    assert not verify_password("first-password", user["password_hash"])

def test_historical_command_type_remains_readable(tmp_path: Path):
    db = tmp_path / "historical-command.db"
    init_db(db)
    repo = Repository(db)
    _record, token = repo.create_enrollment_token("historical", False)
    enrolled = repo.enroll_agent(token, "Demo", "host", "Windows", "127.0.0.1")
    assert enrolled is not None
    agent, _agent_token = enrolled

    historical = repo.create_command(agent["id"], "webcam.snapshot", {"quality": 85}, "a@b.test")

    assert repo.get_command(historical["id"])["type"] == "webcam.snapshot"
    assert any(command["id"] == historical["id"] for command in repo.list_commands())
