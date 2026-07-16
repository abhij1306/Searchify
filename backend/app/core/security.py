# Password hashing, JWT handling, and BYOK secret encryption helpers
# (argon2 / joserfc / Fernet).
from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from cryptography.fernet import Fernet
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey
from joserfc.jwt import JWTClaimsRegistry

from app.core.config import settings

_PASSWORD_HASHER = PasswordHasher()
_ARGON2_PREFIXES = ("$argon2id$", "$argon2i$", "$argon2d$")


class TokenDecodeError(ValueError):
    """Raised when a JWT cannot be decoded or validated."""


def _jwt_key() -> OctKey:
    return OctKey.import_key(settings.jwt_secret_key)


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def _is_argon2_hash(hashed_password: str) -> bool:
    return str(hashed_password or "").startswith(_ARGON2_PREFIXES)


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        if _is_argon2_hash(hashed_password):
            return _PASSWORD_HASHER.verify(hashed_password, password)
        return False
    except (TypeError, ValueError, argon2_exceptions.Argon2Error):
        return False


def password_needs_rehash(hashed_password: str) -> bool:
    try:
        if _is_argon2_hash(hashed_password):
            return _PASSWORD_HASHER.check_needs_rehash(hashed_password)
        return True
    except (TypeError, ValueError, argon2_exceptions.Argon2Error):
        return False


def create_access_token(subject: str, *, token_version: int = 0) -> str:
    expires_at = datetime.now(UTC) + timedelta(hours=settings.jwt_expire_hours)
    payload = {"sub": subject, "exp": expires_at, "ver": token_version}
    return jwt.encode(
        {"alg": settings.jwt_algorithm},
        payload,
        _jwt_key(),
        algorithms=[settings.jwt_algorithm],
    )


def decode_access_token(token: str) -> dict[str, str | int]:
    try:
        decoded = jwt.decode(
            token,
            _jwt_key(),
            algorithms=[settings.jwt_algorithm],
        )
        JWTClaimsRegistry().validate(decoded.claims)
    except JoseError as exc:
        raise TokenDecodeError("Invalid token") from exc
    return dict(decoded.claims)


def _fernet() -> Fernet:
    # Derive a stable 32-byte Fernet key from the configured encryption secret.
    key = settings.encryption_key.encode("utf-8")
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
    return Fernet(derived_key)


def encrypt_secret(value: str) -> str:
    """Fernet-encrypt a BYOK secret for at-rest storage (invariant 6)."""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    """Decrypt a Fernet-encrypted secret. Raises InvalidToken on tamper."""
    token = value.encode("utf-8")
    return _fernet().decrypt(token).decode("utf-8")
