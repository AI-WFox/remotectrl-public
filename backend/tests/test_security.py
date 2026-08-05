import pytest

from app.core.config import Settings
from app.core.security import hash_password, verify_password, make_token, read_token


def test_password_roundtrip():
    encoded = hash_password("secret")
    assert verify_password("secret", encoded)
    assert not verify_password("wrong", encoded)


def test_token_roundtrip():
    token = make_token({"kind": "user", "sub": "1"}, "test-secret")
    assert read_token(token, "test-secret")["sub"] == "1"
    assert read_token(token, "wrong-secret") is None



def test_production_rejects_known_default_credentials():
    settings = Settings(environment="production", secret_key="dev-change-me", default_admin_password="admin12345")
    with pytest.raises(RuntimeError, match="REMOTECTRL_SECRET_KEY.*REMOTECTRL_ADMIN_PASSWORD"):
        settings.validate_runtime()


def test_runtime_rejects_missing_secret_key():
    settings = Settings(environment="development", secret_key="", default_admin_password="test-only-strong-password")
    with pytest.raises(RuntimeError, match="REMOTECTRL_SECRET_KEY"):
        settings.validate_runtime()


def test_production_accepts_explicit_secure_configuration():
    settings = Settings(environment="production", secret_key="test-only-strong-secret-with-32-chars", default_admin_password="test-only-strong-password")
    settings.validate_runtime()