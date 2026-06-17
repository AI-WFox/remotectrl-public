from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.core.config import get_settings
from app.core.security import read_token
from app.services.repository import Repository


def get_repository() -> Repository:
    return Repository(get_settings().database_path)


def require_user(authorization: str | None = Header(default=None)) -> dict[str, str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    payload = read_token(token, get_settings().secret_key)
    if not payload or payload.get("kind") != "user":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return {"user_id": str(payload["sub"]), "email": str(payload["email"])}

