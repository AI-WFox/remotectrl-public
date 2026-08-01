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