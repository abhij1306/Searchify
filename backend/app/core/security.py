# Password hashing, JWT handling, and BYOK secret encryption helpers
# (argon2 / joserfc / Fernet).
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from cryptography.fernet import Fernet
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey
from joserfc.jwt import JWTClaimsRegistry

from app.core.config import settings
from app.core.config.oauth import oauth_settings

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


def create_oauth_state(provider: str) -> str:
    """Mint a signed, short-lived OAuth state token (CSRF/nonce guard).

    Stateless: the state carries the provider plus a random nonce and is
    validated by signature + expiry on the callback, so no server-side
    storage is needed.
    """
    expires_at = datetime.now(UTC) + timedelta(
        seconds=oauth_settings.state_ttl_seconds
    )
    payload = {
        "sub": "oauth-state",
        "provider": provider,
        "nonce": secrets.token_urlsafe(16),
        "exp": expires_at,
    }
    return jwt.encode(
        {"alg": settings.jwt_algorithm},
        payload,
        _jwt_key(),
        algorithms=[settings.jwt_algorithm],
    )


def decode_oauth_state(token: str, provider: str) -> dict[str, str | int]:
    """Decode + validate an OAuth state token for ``provider``.

    Raises ``TokenDecodeError`` on an invalid/expired token or when the token
    was minted for a different provider (or is not an OAuth state token).
    """
    try:
        decoded = jwt.decode(
            token,
            _jwt_key(),
            algorithms=[settings.jwt_algorithm],
        )
        JWTClaimsRegistry().validate(decoded.claims)
    except JoseError as exc:
        raise TokenDecodeError("Invalid OAuth state token") from exc
    claims = dict(decoded.claims)
    if claims.get("sub") != "oauth-state" or claims.get("provider") != provider:
        raise TokenDecodeError("OAuth state provider mismatch")
    return claims


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
