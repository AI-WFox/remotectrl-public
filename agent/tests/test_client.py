from __future__ import annotations

import pytest
import requests
from types import SimpleNamespace

import remotectrl_agent.core.client as client_module

from remotectrl_agent.core.client import AgentClient
from remotectrl_agent.core.config import AgentConfig
from remotectrl_agent.core.handlers import CommandHandlers


def test_enroll_reports_used_or_invalid_token(monkeypatch):
    class Response:
        status_code = 403
        ok = False

    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: Response(), raising=False)
    config = AgentConfig(server_url="https://gateway.example")
    client = AgentClient(config, CommandHandlers(config, lambda _action: None), lambda _status: None, lambda _message: False)

    with pytest.raises(ValueError, match="invalid or has already been used"):
        client.enroll("enroll_used")

def test_enroll_explains_when_saved_gateway_points_to_localhost(monkeypatch):
    class NetworkError(Exception):
        pass

    def fail_request(*_args, **_kwargs):
        raise NetworkError("connection refused")

    monkeypatch.setattr(client_module, "requests", SimpleNamespace(post=fail_request, RequestException=NetworkError))
    config = AgentConfig(server_url="http://127.0.0.1:8766")
    client = AgentClient(config, CommandHandlers(config, lambda _action: None), lambda _status: None, lambda _message: False)

    with pytest.raises(RuntimeError, match="remotectrl-public-demo.onrender.com"):
        client.enroll("enroll_new")
def test_client_retries_unexpected_disconnect_after_local_connect(monkeypatch):
    config = AgentConfig(agent_token="saved-token")
    statuses: list[str] = []
    client = AgentClient(config, CommandHandlers(config, lambda _action: None), statuses.append, lambda _message: False)
    attempts: list[int] = []

    def connect_once():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary network loss")
        client.stop_event.set()

    monkeypatch.setattr(client, "_connect_once", connect_once)
    monkeypatch.setattr(client.stop_event, "wait", lambda _seconds: False)

    client._run_forever()

    assert len(attempts) == 2
    assert "Reconnecting in 1s: temporary network loss" in statuses


def test_client_stops_retrying_when_local_user_disconnects(monkeypatch):
    config = AgentConfig(agent_token="saved-token")
    client = AgentClient(config, CommandHandlers(config, lambda _action: None), lambda _status: None, lambda _message: False)
    attempts: list[int] = []

    def connect_once():
        attempts.append(1)
        raise RuntimeError("network loss")

    monkeypatch.setattr(client, "_connect_once", connect_once)
    monkeypatch.setattr(client.stop_event, "wait", lambda _seconds: client.stop_event.set() or True)

    client._run_forever()

    assert len(attempts) == 1

def test_client_retries_after_clean_gateway_close(monkeypatch):
    config = AgentConfig(agent_token="saved-token")
    statuses: list[str] = []
    client = AgentClient(config, CommandHandlers(config, lambda _action: None), statuses.append, lambda _message: False)
    attempts: list[int] = []

    def connect_once():
        attempts.append(1)
        if len(attempts) == 2:
            client.stop_event.set()

    monkeypatch.setattr(client, "_connect_once", connect_once)
    monkeypatch.setattr(client.stop_event, "wait", lambda _seconds: False)

    client._run_forever()

    assert len(attempts) == 2
    assert "Reconnecting in 1s: gateway connection closed" in statuses
