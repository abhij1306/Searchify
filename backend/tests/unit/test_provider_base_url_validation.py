"""base_url scheme validation on provider-connection write schemas.

A user-settable ``base_url`` is posted to with a bearer token by the answer
engine adapters, so the write schemas reject non-http(s) schemes and plain
http to non-loopback hosts (SSRF / cleartext-downgrade guard).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.providers.schemas import (
    ProviderConnectionCreate,
    ProviderConnectionUpdate,
)


def _create(base_url: str) -> ProviderConnectionCreate:
    return ProviderConnectionCreate(
        transport_provider="openai", api_key="k", base_url=base_url
    )


def test_empty_base_url_is_allowed() -> None:
    assert _create("").base_url == ""
    assert ProviderConnectionUpdate(base_url=None).base_url is None


def test_https_base_url_is_allowed() -> None:
    url = "https://proxy.internal.example.com/v1"
    assert _create(url).base_url == url


def test_http_localhost_is_allowed() -> None:
    url = "http://localhost:8080/v1"
    assert _create(url).base_url == url
    assert ProviderConnectionUpdate(base_url="http://127.0.0.1/v1")


@pytest.mark.parametrize(
    "url",
    [
        "http://internal-service.local/secret",  # http to non-loopback
        "ftp://example.com",  # non-web scheme
        "file:///etc/passwd",  # local file scheme
        "gopher://example.com",  # exotic scheme
        "example.com/v1",  # missing scheme
    ],
)
def test_unsafe_base_url_is_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        _create(url)
    with pytest.raises(ValidationError):
        ProviderConnectionUpdate(base_url=url)
