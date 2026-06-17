from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


CommandStatus = Literal[
    "queued",
    "sent",
    "pending_approval",
    "running",
    "succeeded",
    "failed",
    "denied",
]


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class EnrollmentTokenCreate(BaseModel):
    label: str = "Demo enrollment"
    reusable: bool = False


class EnrollmentTokenResponse(BaseModel):
    id: str
    token: str
    label: str
    reusable: bool
    created_at: str


class AgentEnrollment(BaseModel):
    enrollment_token: str
    name: str
    hostname: str
    os: str


class AgentEnrollmentResponse(BaseModel):
    agent_id: str
    agent_token: str


class AgentPublic(BaseModel):
    id: str
    name: str
    hostname: str
    os: str
    status: str
    ip_address: str | None = None
    last_seen_at: str | None = None
    created_at: str


class CommandCreate(BaseModel):
    agent_id: str
    type: str = Field(pattern=r"^[a-z]+\.[a-z_]+(\.[a-z_]+)?$")
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandPublic(BaseModel):
    id: str
    agent_id: str
    type: str
    payload: dict[str, Any]
    requires_approval: bool
    status: CommandStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_by: str
    created_at: str
    updated_at: str


class AuditEventPublic(BaseModel):
    id: str
    actor: str
    action: str
    agent_id: str | None = None
    command_id: str | None = None
    detail: dict[str, Any]
    created_at: str

