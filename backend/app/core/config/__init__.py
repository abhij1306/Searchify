# Centralized application settings (invariant 1: all config lives here).
from __future__ import annotations

import logging
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config/__init__.py -> parents[3] == backend/
BASE_DIR = Path(__file__).resolve().parents[3]
# repo root (Searchify/) holds the shared .env used by docker + local dev.
PROJECT_ROOT = BASE_DIR.parent

_INSECURE_DEFAULTS = {
    "change-me",
    "change-me-32-bytes-minimum-change-me",
    "replace-with-64-byte-random-secret",
    "replace-with-32-byte-minimum-secret",
}


class Settings(BaseSettings):
    """Application settings singleton, loaded from environment / .env.

    Values are read from the process environment first, then the repo-root and
    backend-local ``.env`` files. Every tunable knob (secrets, model ids,
    thresholds, timeouts) belongs here rather than inline in service code.
    """

    model_config = SettingsConfigDict(
        # Support both repo-root and backend-local .env files.
        env_file=(str(PROJECT_ROOT / ".env"), str(BASE_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Searchify"
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "app_env"),
    )
    backend_host: str = "127.0.0.1"
    backend_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("BACKEND_PORT", "backend_port"),
    )
    frontend_url: str = Field(
        default="http://127.0.0.1:3000",
        validation_alias=AliasChoices("FRONTEND_URL", "frontend_url"),
    )
    # Comma-separated explicit CORS origins; overrides frontend_url expansion.
    frontend_origins: str = ""

    # --- Auth / crypto (invariant 6) ---
    jwt_secret_key: str = Field(
        default="change-me-32-bytes-minimum-change-me",
        validation_alias=AliasChoices("JWT_SECRET_KEY", "jwt_secret_key"),
    )
    jwt_algorithm: str = Field(
        default="HS256",
        validation_alias=AliasChoices("JWT_ALGORITHM", "jwt_algorithm"),
    )
    jwt_expire_hours: int = Field(
        default=24,
        validation_alias=AliasChoices("JWT_EXPIRE_HOURS", "jwt_expire_hours"),
    )
    # Session cookie name for the HttpOnly JWT (set by B2).
    session_cookie_name: str = "searchify_session"
    encryption_key: str = Field(
        default="replace-with-32-byte-minimum-secret",
        validation_alias=AliasChoices("ENCRYPTION_KEY", "encryption_key"),
    )

    # --- Database ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/searchify",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )
    db_pool_size: int = Field(
        default=5, validation_alias=AliasChoices("DB_POOL_SIZE", "db_pool_size")
    )
    db_max_overflow: int = Field(
        default=10, validation_alias=AliasChoices("DB_MAX_OVERFLOW", "db_max_overflow")
    )
    db_pool_recycle_seconds: int = Field(
        default=600,
        validation_alias=AliasChoices(
            "DB_POOL_RECYCLE_SECONDS", "db_pool_recycle_seconds"
        ),
    )
    db_pool_timeout_seconds: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "DB_POOL_TIMEOUT_SECONDS", "db_pool_timeout_seconds"
        ),
    )
    db_pool_pre_ping: bool = Field(
        default=True,
        validation_alias=AliasChoices("DB_POOL_PRE_PING", "db_pool_pre_ping"),
    )

    request_id_header: str = Field(
        default="X-Request-ID",
        validation_alias=AliasChoices("REQUEST_ID_HEADER", "request_id_header"),
    )

    # --- Observability (optional Logfire) ---
    logfire_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("LOGFIRE_ENABLED", "logfire_enabled"),
    )
    logfire_token: str = Field(
        default="",
        validation_alias=AliasChoices("LOGFIRE_TOKEN", "logfire_token"),
    )
    logfire_service_name: str = Field(
        default="searchify-backend",
        validation_alias=AliasChoices("LOGFIRE_SERVICE_NAME", "logfire_service_name"),
    )
    logfire_environment: str = Field(
        default="",
        validation_alias=AliasChoices("LOGFIRE_ENVIRONMENT", "logfire_environment"),
    )
    logfire_enabled_in_tests: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "LOGFIRE_ENABLED_IN_TESTS", "logfire_enabled_in_tests"
        ),
    )


def _load_settings() -> Settings:
    # BaseSettings reads values from environment/.env at runtime.
    return Settings()


settings = _load_settings()


def _check_secret_defaults() -> None:
    """Warn (or crash outside dev/test) if default secrets are still set."""
    logger = logging.getLogger("app.core.config")
    env = str(settings.app_env or "development").strip().lower()
    is_non_dev = env not in {"", "development", "dev", "local", "test", "testing"}
    issues: list[str] = []
    if settings.jwt_secret_key in _INSECURE_DEFAULTS:
        issues.append("jwt_secret_key is set to a default value")
    if settings.encryption_key in _INSECURE_DEFAULTS:
        issues.append("encryption_key is set to a default value")
    if not issues:
        return
    msg = (
        "SECURITY WARNING: insecure default secrets detected: "
        + "; ".join(issues)
        + '. Generate secure values: '
        + 'python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )
    if is_non_dev:
        raise RuntimeError(msg)
    logger.warning(msg)


_check_secret_defaults()


def get_frontend_origins() -> list[str]:
    """Resolve the allowed CORS origins for the FastAPI CORS middleware."""
    if settings.frontend_origins.strip():
        return [
            origin.strip()
            for origin in settings.frontend_origins.split(",")
            if origin.strip()
        ]

    origin = settings.frontend_url.rstrip("/")
    variants = {origin}
    if "127.0.0.1" in origin:
        variants.add(origin.replace("127.0.0.1", "localhost"))
    if "localhost" in origin:
        variants.add(origin.replace("localhost", "127.0.0.1"))
    return sorted(variants)
