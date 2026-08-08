from fastapi.testclient import TestClient

from app import main as main_module
from app.core.db import init_db
from app.main import app, settings
from app.services.repository import Repository


def test_api_login_enroll_and_offline_command(tmp_path):
    settings.database_path = tmp_path / "api.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("qa-admin@example.invalid", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("test", reusable=True)

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "qa-admin@example.invalid", "password": "admin12345"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    bootstrap = client.get("/api/bootstrap")
    assert bootstrap.status_code == 200
    capabilities = {item["type"]: item for item in bootstrap.json()["capabilities"]}
    assert capabilities["process.list"]["requires_approval"] is True
    assert capabilities["app.list"]["requires_approval"] is True
    assert capabilities["files.list"]["requires_approval"] is True
    assert capabilities["screen.live.start"]["requires_approval"] is True
    assert capabilities["files.roots"]["requires_approval"] is True
    assert capabilities["screen.live.stop"]["requires_approval"] is True
    assert capabilities["webcam.live.stop"]["requires_approval"] is True
    assert capabilities["webcam.list"]["requires_approval"] is True
    assert "webcam.snapshot" not in capabilities
    assert not any(command.startswith("keycapture.") for command in capabilities)
    assert capabilities["activity.start"]["requires_approval"] is True
    assert capabilities["activity.stop"]["requires_approval"] is True
    assert capabilities["power.sleep"]["requires_approval"] is True
    assert capabilities["power.status"]["requires_approval"] is True
    assert "power.logout" not in capabilities

    one_time_token = client.post(
        "/api/enrollment-tokens",
        headers=headers,
        json={"label": "one-time", "reusable": False},
    )
    assert one_time_token.status_code == 200
    assert one_time_token.json()["reusable"] is False
    one_time_enrollment = client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": one_time_token.json()["token"],
            "name": "One Time Agent",
            "hostname": "one-time-host",
            "os": "Windows",
        },
    )
    assert one_time_enrollment.status_code == 200
    reused_one_time_token = client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": one_time_token.json()["token"],
            "name": "Should Not Enroll",
            "hostname": "blocked-host",
            "os": "Windows",
        },
    )
    assert reused_one_time_token.status_code == 403

    enrolled = client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": enrollment_token,
            "name": "API Test Agent",
            "hostname": "api-host",
            "os": "Windows",
        },
    )
    assert enrolled.status_code == 200
    agent_id = enrolled.json()["agent_id"]

    command = client.post(
        "/api/commands",
        headers=headers,
        json={"agent_id": agent_id, "type": "process.list", "payload": {}},
    )
    assert command.status_code == 200
    assert command.json()["requires_approval"] is True
    assert command.json()["status"] == "failed"
    assert command.json()["error"] == "Agent offline"

    for command_type in ("system.exec", "power.logout"):
        unsupported = client.post(
            "/api/commands",
            headers=headers,
            json={"agent_id": agent_id, "type": command_type, "payload": {}},
        )
        assert unsupported.status_code == 400
        assert unsupported.json()["detail"] == "Unsupported command type"


def test_cleanup_offline_agents_and_reenroll_reuses_identity(tmp_path):
    settings.database_path = tmp_path / "cleanup.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("qa-admin@example.invalid", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("test", reusable=True)

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "qa-admin@example.invalid", "password": "admin12345"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    first = client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": enrollment_token,
            "name": "Same Agent",
            "hostname": "same-host",
            "os": "Windows",
        },
    ).json()
    second = client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": enrollment_token,
            "name": "Same Agent",
            "hostname": "same-host",
            "os": "Windows 11",
        },
    ).json()
    assert first["agent_id"] == second["agent_id"]
    assert len(repo.list_agents()) == 1

    _other_record, other_token = repo.create_enrollment_token("other", reusable=True)
    client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": other_token,
            "name": "Offline Old Agent",
            "hostname": "old-host",
            "os": "Windows",
        },
    )

    cleanup = client.delete("/api/agents/offline", headers=headers)
    assert cleanup.status_code == 200
    assert cleanup.json()["deleted"] == 2
    assert repo.list_agents() == []


def test_delete_single_agent_removes_record_and_commands(tmp_path):
    settings.database_path = tmp_path / "delete-agent.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("qa-admin@example.invalid", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("test", reusable=True)

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "qa-admin@example.invalid", "password": "admin12345"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    enrolled = client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": enrollment_token,
            "name": "Delete Me",
            "hostname": "delete-host",
            "os": "Windows",
        },
    )
    agent_id = enrolled.json()["agent_id"]
    command = client.post(
        "/api/commands",
        headers=headers,
        json={"agent_id": agent_id, "type": "process.list", "payload": {}},
    )
    assert command.status_code == 200

    deleted = client.delete(f"/api/agents/{agent_id}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert repo.get_agent(agent_id) is None
    assert repo.list_agent_commands(agent_id) == []


def test_command_submission_deduplicates_active_requests_and_rate_limits_bursts(tmp_path, monkeypatch):
    settings.database_path = tmp_path / "command-pressure.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("pressure@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("pressure", reusable=True)

    async def pretend_online(_agent_id, _message):
        return True

    monkeypatch.setattr(main_module.manager, "send_to_agent", pretend_online)
    main_module._command_attempts.clear()
    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "pressure@remotectrl.local", "password": "admin12345"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    enrolled = client.post(
        "/api/agents/enroll",
        json={
            "enrollment_token": enrollment_token,
            "name": "Pressure Agent",
            "hostname": "pressure-host",
            "os": "Windows",
        },
    )
    agent_id = enrolled.json()["agent_id"]

    payload = {"preset": "notepad", "mode": "focus_existing"}
    first = client.post("/api/commands", headers=headers, json={"agent_id": agent_id, "type": "app.start", "payload": payload})
    duplicate = client.post("/api/commands", headers=headers, json={"agent_id": agent_id, "type": "app.start", "payload": payload})

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    assert len(repo.list_agent_commands(agent_id)) == 1

    main_module._command_attempts.clear()
    monkeypatch.setattr(main_module.time, "monotonic", lambda: 100.0)
    responses = [
        client.post(
            "/api/commands",
            headers=headers,
            json={"agent_id": agent_id, "type": "process.kill", "payload": {"pid": 1000 + index}},
        )
        for index in range(main_module.COMMAND_RATE_LIMIT + 1)
    ]
    assert all(response.status_code == 200 for response in responses[:-1])
    assert responses[-1].status_code == 429
    assert responses[-1].headers["retry-after"] == "1"

def test_command_rate_limit_buckets_are_bounded_and_expire(monkeypatch):
    main_module._command_attempts.clear()
    main_module._command_rate_last_cleanup = 0.0
    now = {"value": 100.0}
    monkeypatch.setattr(main_module.time, "monotonic", lambda: now["value"])

    for index in range(main_module.COMMAND_RATE_MAX_BUCKETS):
        assert main_module._consume_command_budget(f"operator:agent-{index}") is True
    assert main_module._consume_command_budget("operator:overflow") is False
    assert len(main_module._command_attempts) == main_module.COMMAND_RATE_MAX_BUCKETS

    now["value"] += main_module.COMMAND_RATE_WINDOW_SECONDS
    assert main_module._consume_command_budget("operator:overflow") is True
    assert list(main_module._command_attempts) == ["operator:overflow"]


def test_repository_fails_active_commands_when_agent_disconnects(tmp_path):
    database = tmp_path / "disconnect-commands.db"
    init_db(database)
    repo = Repository(database)
    _record, enrollment_token = repo.create_enrollment_token("disconnect", reusable=True)
    enrolled = repo.enroll_agent(enrollment_token, "Agent", "host", "Windows", None)
    assert enrolled is not None
    agent, _token = enrolled
    command = repo.create_command(agent["id"], "screen.live.start", {}, "operator@test")
    repo.update_command(command["id"], "running")

    failed = repo.fail_active_commands_for_agent(agent["id"])

    assert [item["id"] for item in failed] == [command["id"]]
    assert repo.get_command(command["id"])["status"] == "failed"
    assert repo.get_command(command["id"])["error"] == "Agent disconnected"
