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
    secret_key: str = os.getenv("REMOTECTRL_SECRET_KEY", "")
    cors_origins: list[str] = parse_cors_origins()
    default_admin_email: str = os.getenv("REMOTECTRL_ADMIN_EMAIL", "")
    default_admin_password: str = os.getenv("REMOTECTRL_ADMIN_PASSWORD", "")
    environment: str = os.getenv("REMOTECTRL_ENV", "development")

    def validate_runtime(self) -> None:
        unsafe = []
        if not self.secret_key:
            unsafe.append("REMOTECTRL_SECRET_KEY")
        if not self.default_admin_email:
            unsafe.append("REMOTECTRL_ADMIN_EMAIL")
        if not self.default_admin_password:
            unsafe.append("REMOTECTRL_ADMIN_PASSWORD")
        if self.environment.lower() == "production":
            if len(self.secret_key) < 32 and "REMOTECTRL_SECRET_KEY" not in unsafe:
                unsafe.append("REMOTECTRL_SECRET_KEY")
            if len(self.default_admin_password) < 12 and "REMOTECTRL_ADMIN_PASSWORD" not in unsafe:
                unsafe.append("REMOTECTRL_ADMIN_PASSWORD")
        if unsafe:
            scope = "Production" if self.environment.lower() == "production" else "Runtime"
            raise RuntimeError(f"{scope} configuration is missing secure values for: {', '.join(unsafe)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
