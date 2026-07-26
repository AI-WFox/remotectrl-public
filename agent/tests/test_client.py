from __future__ import annotations

import pytest
import requests

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