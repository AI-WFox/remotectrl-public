import asyncio

import pytest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.db import init_db
from app.main import app, handle_agent_message, manager, settings
from app.services.repository import Repository


@pytest.fixture(autouse=True)
def reset_realtime_manager():
    manager.agent_sockets.clear()
    manager.dashboard_sockets.clear()
    manager.agent_sessions.clear()
    manager.dashboard_tickets.clear()
    yield
    manager.agent_sockets.clear()
    manager.dashboard_sockets.clear()
    manager.agent_sessions.clear()
    manager.dashboard_tickets.clear()


def dashboard_ws_url(client: TestClient) -> str:
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@remotectrl.local", "password": "admin12345"},
    )
    ticket = client.post(
        "/api/auth/ws-ticket",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert ticket.status_code == 200
    return f"/ws/dashboard?ticket={ticket.json()['ticket']}"


def authenticate_agent(websocket, agent_token: str) -> dict:
    websocket.send_json({"type": "authenticate", "token": agent_token})
    return websocket.receive_json()


def test_agent_websocket_receives_command_and_returns_result(tmp_path):
    settings.database_path = tmp_path / "ws.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("ws", reusable=True)

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
            "name": "WS Agent",
            "hostname": "ws-host",
            "os": "Windows",
        },
    ).json()

    with client.websocket_connect("/ws/agent") as websocket:
        hello = authenticate_agent(websocket, enrolled["agent_token"])
        assert hello["agent_id"] == enrolled["agent_id"]

        command_response = client.post(
            "/api/commands",
            headers=headers,
            json={"agent_id": enrolled["agent_id"], "type": "process.list", "payload": {}},
        )
        assert command_response.status_code == 200
        command_id = command_response.json()["id"]
        routed = websocket.receive_json()
        assert routed["type"] == "command"
        assert routed["command_id"] == command_id
        assert routed["command_type"] == "process.list"

        websocket.send_json(
            {
                "type": "command_result",
                "command_id": command_id,
                "agent_id": enrolled["agent_id"],
                "ok": True,
                "payload": {"count": 0, "items": []},
            }
        )

    command = repo.get_command(command_id)
    assert command["status"] == "succeeded"
    assert command["result"] == {"count": 0, "items": []}


def test_delete_online_agent_closes_and_removes_record(tmp_path):
    settings.database_path = tmp_path / "delete-online.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("ws-delete", reusable=True)

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
            "name": "Online Delete Agent",
            "hostname": "online-delete-host",
            "os": "Windows",
        },
    ).json()

    with client.websocket_connect("/ws/agent") as websocket:
        hello = authenticate_agent(websocket, enrolled["agent_token"])
        assert hello["agent_id"] == enrolled["agent_id"]
        listed = client.get("/api/agents", headers=headers)
        assert listed.json()[0]["status"] == "online"

        deleted = client.delete(f"/api/agents/{enrolled['agent_id']}", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert repo.get_agent(enrolled["agent_id"]) is None


def test_command_routes_only_to_selected_agent_when_two_agents_online(tmp_path):
    settings.database_path = tmp_path / "ws-two-agents.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("ws-two", reusable=True)

    client = TestClient(app)
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@remotectrl.local", "password": "admin12345"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    first = client.post(
        "/api/agents/enroll",
        json={"enrollment_token": enrollment_token, "name": "Agent A", "hostname": "host-a", "os": "Windows"},
    ).json()
    second = client.post(
        "/api/agents/enroll",
        json={"enrollment_token": enrollment_token, "name": "Agent B", "hostname": "host-b", "os": "Windows"},
    ).json()

    with client.websocket_connect("/ws/agent") as ws_a:
        authenticate_agent(ws_a, first["agent_token"])
        with client.websocket_connect("/ws/agent") as ws_b:
            authenticate_agent(ws_b, second["agent_token"])
            response = client.post(
                "/api/commands",
                headers=headers,
                json={"agent_id": second["agent_id"], "type": "files.roots", "payload": {}},
            )
            assert response.status_code == 200
            routed = ws_b.receive_json()
            assert routed["agent_id"] == second["agent_id"]
            assert routed["command_type"] == "files.roots"
            ws_a.close()


def test_activity_events_reach_dashboard_only_as_realtime_messages(tmp_path):
    settings.database_path = tmp_path / "activity-events.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("activity", reusable=True)

    client = TestClient(app)
    enrolled = client.post(
        "/api/agents/enroll",
        json={"enrollment_token": enrollment_token, "name": "Activity Agent", "hostname": "activity-host", "os": "Windows"},
    ).json()

    with client.websocket_connect(dashboard_ws_url(client)) as dashboard:
        assert dashboard.receive_json()["role"] == "dashboard"
        assert dashboard.receive_json()["type"] == "agent.session_snapshot"
        with client.websocket_connect("/ws/agent") as agent:
            authenticate_agent(agent, enrolled["agent_token"])
            dashboard.receive_json()  # agent.online
            agent.send_json(
                {
                    "type": "activity_event",
                    "agent_id": enrolled["agent_id"],
                    "event": {"time": "2026-07-26T20:00:00", "type": "active_window.changed", "detail": {"title": "Notepad"}},
                }
            )
            event = dashboard.receive_json()
            assert event == {
                "type": "activity.event",
                "agent_id": enrolled["agent_id"],
                "event": {"time": "2026-07-26T20:00:00", "type": "active_window.changed", "detail": {"title": "Notepad"}},
            }

def test_agent_metadata_session_and_folder_events_reach_dashboard(tmp_path):
    settings.database_path = tmp_path / "agent-events.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("agent-events", reusable=True)
    client = TestClient(app)
    enrolled = client.post(
        "/api/agents/enroll",
        json={"enrollment_token": enrollment_token, "name": "Original Agent", "hostname": "events-host", "os": "Windows"},
    ).json()

    with client.websocket_connect(dashboard_ws_url(client)) as dashboard:
        assert dashboard.receive_json()["role"] == "dashboard"
        dashboard.receive_json()
        with client.websocket_connect("/ws/agent") as agent:
            authenticate_agent(agent, enrolled["agent_token"])
            dashboard.receive_json()  # agent.online

            agent.send_json({"type": "agent_metadata", "name": "Renamed Agent"})
            metadata = dashboard.receive_json()
            assert metadata["type"] == "agent.metadata"
            assert metadata["agent"]["name"] == "Renamed Agent"
            assert repo.get_agent(enrolled["agent_id"])["name"] == "Renamed Agent"

            agent.send_json({"type": "agent_session_state", "sessions": {"screen": True, "webcam": False, "activity": True}, "source": "local"})
            session = dashboard.receive_json()
            assert session == {
                "type": "agent.session_state",
                "agent_id": enrolled["agent_id"],
                "sessions": {"screen": True, "webcam": False, "activity": True},
                "source": "local",
            }

            agent.send_json({"type": "agent_config_invalidated", "kind": "allowed_folders"})
            invalidated = dashboard.receive_json()
            assert invalidated == {"type": "agent.config_invalidated", "agent_id": enrolled["agent_id"], "kind": "allowed_folders"}

def test_agent_cannot_complete_another_agents_command(tmp_path):
    settings.database_path = tmp_path / "cross-agent-command.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    _record, enrollment_token = repo.create_enrollment_token("cross-agent", reusable=True)
    first, _first_token = repo.enroll_agent(enrollment_token, "Agent A", "host-a", "Windows", "127.0.0.1")
    second, _second_token = repo.enroll_agent(enrollment_token, "Agent B", "host-b", "Windows", "127.0.0.1")
    command = repo.create_command(second["id"], "process.list", {}, "admin@remotectrl.local")

    asyncio.run(
        handle_agent_message(
            repo,
            first["id"],
            {
                "type": "command_result",
                "command_id": command["id"],
                "ok": True,
                "payload": {"count": 0, "items": []},
            },
        )
    )

    assert repo.get_command(command["id"])["status"] == "queued"
    audit = repo.list_audit()
    assert any(
        event["action"] == "agent.command_rejected"
        and event["detail"].get("reason") == "command_agent_mismatch"
        for event in audit
    )

def test_agent_cannot_report_command_error_for_another_agent(tmp_path, monkeypatch):
    settings.database_path = tmp_path / "cross-agent-command-error.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    _record, enrollment_token = repo.create_enrollment_token("cross-agent-error", reusable=True)
    first, _first_token = repo.enroll_agent(enrollment_token, "Agent A", "host-a", "Windows", "127.0.0.1")
    second, _second_token = repo.enroll_agent(enrollment_token, "Agent B", "host-b", "Windows", "127.0.0.1")
    command = repo.create_command(second["id"], "app.start", {}, "admin@remotectrl.local")
    broadcasts: list[dict] = []

    async def record_broadcast(message: dict) -> None:
        broadcasts.append(message)

    from app import main as main_module
    monkeypatch.setattr(main_module.manager, "broadcast_dashboard", record_broadcast)

    asyncio.run(
        handle_agent_message(
            repo,
            first["id"],
            {
                "type": "agent_command_error",
                "command_id": command["id"],
                "command_type": "app.start",
                "error": "forged error",
            },
        )
    )

    assert broadcasts == []
    assert not any(event["action"] == "agent.command_error" for event in repo.list_audit())
    assert any(
        event["action"] == "agent.command_rejected"
        and event["detail"].get("reason") == "command_agent_mismatch"
        for event in repo.list_audit()
    )

def test_agent_cannot_forward_a_stream_frame_for_another_agents_command(tmp_path, monkeypatch):
    settings.database_path = tmp_path / "cross-agent-stream-frame.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    _record, enrollment_token = repo.create_enrollment_token("cross-agent-stream", reusable=True)
    first, _first_token = repo.enroll_agent(enrollment_token, "Agent A", "host-a", "Windows", "127.0.0.1")
    second, _second_token = repo.enroll_agent(enrollment_token, "Agent B", "host-b", "Windows", "127.0.0.1")
    command = repo.create_command(second["id"], "screen.live.start", {}, "admin@remotectrl.local")
    broadcasts: list[dict] = []

    async def record_broadcast(message: dict) -> None:
        broadcasts.append(message)

    from app import main as main_module
    monkeypatch.setattr(main_module.manager, "broadcast_dashboard", record_broadcast)

    asyncio.run(
        handle_agent_message(
            repo,
            first["id"],
            {
                "type": "stream_frame",
                "command_id": command["id"],
                "agent_id": second["id"],
                "mime": "image/jpeg",
                "frame": "not-a-real-frame",
            },
        )
    )

    assert broadcasts == []
    assert any(
        event["action"] == "agent.command_rejected"
        and event["detail"].get("reason") == "command_agent_mismatch"
        for event in repo.list_audit()
    )


def test_dashboard_websocket_requires_one_time_ticket(tmp_path):
    settings.database_path = tmp_path / "dashboard-auth.db"
    init_db(settings.database_path)
    repo = Repository(settings.database_path)
    repo.ensure_admin("admin@remotectrl.local", "admin12345")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as unauthorized:
        with client.websocket_connect("/ws/dashboard") as dashboard:
            dashboard.receive_json()
    assert unauthorized.value.code == 4401

    url = dashboard_ws_url(client)
    with client.websocket_connect(url) as dashboard:
        assert dashboard.receive_json() == {"type": "hello", "role": "dashboard"}
        assert dashboard.receive_json()["type"] == "agent.session_snapshot"

    with pytest.raises(WebSocketDisconnect) as reused:
        with client.websocket_connect(url) as dashboard:
            dashboard.receive_json()
    assert reused.value.code == 4401


def test_agent_websocket_requires_authentication_message(tmp_path):
    settings.database_path = tmp_path / "agent-auth.db"
    init_db(settings.database_path)
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws/agent") as websocket:
            websocket.send_json({"type": "authenticate", "token": "invalid"})
            websocket.receive_json()
    assert rejected.value.code == 4403