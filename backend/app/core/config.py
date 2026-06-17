from functools import lru_cache
from pathlib import Path
from pydantic import BaseModel
import os


def parse_cors_origins() -> list[str]:
    raw = os.getenv("REMOTECTRL_CORS_ORIGINS")
    if not raw:
        return ["http://localhost:5173", "http://127.0.0.1:5173"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class Settings(BaseModel):
    app_name: str = "RemoteCtrl Gateway"
    database_path: Path = Path(os.getenv("REMOTECTRL_DB", "remotectrl.db"))
    secret_key: str = os.getenv("REMOTECTRL_SECRET_KEY", "dev-change-me")
    cors_origins: list[str] = parse_cors_origins()
    default_admin_email: str = os.getenv("REMOTECTRL_ADMIN_EMAIL", "admin@remotectrl.local")
    default_admin_password: str = os.getenv("REMOTECTRL_ADMIN_PASSWORD", "admin12345")


@lru_cache
def get_settings() -> Settings:
    return Settings()
