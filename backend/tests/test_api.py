from fastapi.testclient import TestClient

from app.core.db import init_db
from app.main import app, settings
from app.services.repository import Repository


def test_api_login_enroll_and_offline_command(tmp_path):
    settings.database_path = tmp_path / "api.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("test", reusable=True)

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@remotectrl.local", "password": "admin12345"},
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
    assert capabilities["keycapture.stop"]["requires_approval"] is True
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
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("test", reusable=True)

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@remotectrl.local", "password": "admin12345"},
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
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("test", reusable=True)

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@remotectrl.local", "password": "admin12345"},
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
