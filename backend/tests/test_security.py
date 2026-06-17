from app.core.security import hash_password, verify_password, make_token, read_token


def test_password_roundtrip():
    encoded = hash_password("secret")
    assert verify_password("secret", encoded)
    assert not verify_password("wrong", encoded)


def test_token_roundtrip():
    token = make_token({"kind": "user", "sub": "1"}, "test-secret")
    assert read_token(token, "test-secret")["sub"] == "1"
    assert read_token(token, "wrong-secret") is None

