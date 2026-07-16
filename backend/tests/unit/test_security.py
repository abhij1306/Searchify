"""Unit tests for core security helpers: argon2, JWT, Fernet."""
from __future__ import annotations

import pytest

from app.core.security import (
    TokenDecodeError,
    create_access_token,
    decode_access_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password_roundtrip() -> None:
    hashed = hash_password("s3cret-pw")
    assert hashed.startswith("$argon2")
    assert verify_password("s3cret-pw", hashed) is True
    assert verify_password("wrong-pw", hashed) is False


def test_verify_password_rejects_garbage_hash() -> None:
    assert verify_password("anything", "not-a-hash") is False


def test_access_token_roundtrip() -> None:
    token = create_access_token("user-uuid", token_version=1)
    claims = decode_access_token(token)
    assert claims["sub"] == "user-uuid"
    assert claims["ver"] == 1


def test_decode_rejects_tampered_token() -> None:
    token = create_access_token("user-uuid")
    with pytest.raises(TokenDecodeError):
        decode_access_token(token + "tampered")


def test_encrypt_secret_roundtrip_and_opacity() -> None:
    ciphertext = encrypt_secret("byok-api-key")
    # Ciphertext must not leak the plaintext (invariant 6).
    assert "byok-api-key" not in ciphertext
    assert decrypt_secret(ciphertext) == "byok-api-key"
