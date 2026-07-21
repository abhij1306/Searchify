"""Provider-connection request/response schemas (all ids string UUID).

Invariant 6: the BYOK secret is WRITE-ONLY. ``api_key`` is accepted on
create/update but no response DTO in this module exposes the key or its
ciphertext — only a boolean ``api_key_set`` flag. Whether a key is present is
safe to surface; the value is not.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Loopback hosts allowed over plain http (local self-hosted proxy in dev). Every
# other host must use https so a stored base_url cannot downgrade a
# bearer-authenticated provider call to cleartext or a non-web scheme.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _validate_base_url(value: str | None) -> str | None:
    """Reject base_url values with an unsafe scheme (SSRF/downgrade guard).

    Empty / ``None`` means "use the provider default" and is always allowed.
    Otherwise only ``https`` is accepted, except plain ``http`` to a loopback
    host for local self-hosted proxies. A missing scheme or host is rejected so
    the adapter never posts a bearer-authenticated request to an ambiguous URL.
    """
    if value is None or value == "":
        return value
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("base_url must use http or https")
    if not parts.hostname:
        raise ValueError("base_url must include a host")
    if scheme == "http" and parts.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("base_url must use https (http allowed only for localhost)")
    return value


# Enumerations mirror provider_catalog (kept as Literals so FastAPI validates
# the request body; the service re-validates against the catalog for routes).
#
# ``ActiveTransportProvider`` is the complete write/create transport surface.
ActiveTransportProvider = Literal["openai", "anthropic", "google"]
# Backwards-compatible alias used by the create/write path.
TransportProvider = ActiveTransportProvider
LogicalEngine = Literal["chatgpt", "gemini", "claude"]


class ProviderRouteInput(BaseModel):
    logical_engine: LogicalEngine
    transport_model: str = Field(default="", max_length=255)
    is_default: bool = False


class ProviderRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    logical_engine: str
    transport_provider: str
    transport_model: str
    is_default: bool
    # Whether this route is executable.
    active: bool = True


class ProviderConnectionCreate(BaseModel):
    label: str = Field(default="", max_length=255)
    transport_provider: TransportProvider
    # WRITE-ONLY BYOK secret (invariant 6). Never echoed in any response.
    api_key: str = Field(min_length=1)
    base_url: str = Field(default="", max_length=1024)
    active: bool = True
    routes: list[ProviderRouteInput] = Field(default_factory=list)

    _check_base_url = field_validator("base_url")(_validate_base_url)


class ProviderConnectionUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    # Optional rotation. Omitted / empty leaves the stored key unchanged.
    api_key: str | None = Field(default=None)
    base_url: str | None = Field(default=None, max_length=1024)
    active: bool | None = None
    routes: list[ProviderRouteInput] | None = None

    _check_base_url = field_validator("base_url")(_validate_base_url)


class ProviderConnectionResponse(BaseModel):
    """Connection DTO. Deliberately has NO api_key field (invariant 6)."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    label: str
    transport_provider: str
    base_url: str
    active: bool
    # Presence flag only — the key value itself is never serialized.
    api_key_set: bool
    last_tested_at: datetime | None
    last_test_status: str
    routes: list[ProviderRouteResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ProviderConnectionTestResponse(BaseModel):
    """Result of a ``POST /provider-connections/{id}/test`` call."""

    connection_id: uuid.UUID
    status: str
    error_code: str = ""
    detail: str = ""
    latency_ms: int | None = None
    logical_engine: str = ""
    transport_provider: str = ""
    transport_model: str = ""
    tested_at: datetime


class ProviderCatalogRoute(BaseModel):
    transport_provider: str
    default_model: str


class ProviderCatalogEngine(BaseModel):
    logical_engine: str
    routes: list[ProviderCatalogRoute]


class ProviderCatalogResponse(BaseModel):
    transports: list[str]
    engines: list[ProviderCatalogEngine]
