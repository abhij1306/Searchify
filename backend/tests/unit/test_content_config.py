"""Content config: spec shape, output types, caps, and secret handling."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config.content import (
    CONTENT_DEFAULT_OUTPUT_TYPE,
    CONTENT_LIST_DEFAULT_LIMIT,
    CONTENT_LIST_MAX_LIMIT,
    CONTENT_MAX_ATTEMPTS,
    CONTENT_OUTPUT_TYPES,
    CONTENT_PROMPT_MAX_LEN,
    CONTENT_QUEUE_SPEC,
    ContentSettings,
    _content_claim_order,
)
from app.models.content import ContentGeneration


def test_queue_spec_resolves_content_model() -> None:
    assert CONTENT_QUEUE_SPEC.model is ContentGeneration
    assert CONTENT_QUEUE_SPEC.lease_ttl() > 0
    assert CONTENT_QUEUE_SPEC.max_attempts_error == "max_attempts_exceeded"


def test_claim_order_is_deterministic_priority_fifo_position() -> None:
    order = _content_claim_order(ContentGeneration)
    assert len(order) == 3
    rendered = [str(clause) for clause in order]
    assert "priority DESC" in rendered[0]
    assert "available_at ASC" in rendered[1]
    assert "randomized_position ASC" in rendered[2]


def test_output_type_vocabulary() -> None:
    assert CONTENT_DEFAULT_OUTPUT_TYPE in CONTENT_OUTPUT_TYPES
    assert CONTENT_OUTPUT_TYPES == frozenset({"website_page"})


def test_list_limit_constants() -> None:
    assert 0 < CONTENT_LIST_DEFAULT_LIMIT <= CONTENT_LIST_MAX_LIMIT


def test_prompt_cap_and_retry_budget_positive() -> None:
    assert CONTENT_PROMPT_MAX_LEN > 0
    assert CONTENT_MAX_ATTEMPTS >= 1


def test_api_key_is_secretstr_and_defaults_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A developer's real key in the env must not leak into this test.
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    fresh = ContentSettings(_env_file=None)
    assert isinstance(fresh.mistral_api_key, SecretStr)
    # The default is empty (provider not configured).
    assert fresh.mistral_api_key.get_secret_value() == ""

    # A non-empty key never leaks through str()/repr() (invariant 6).
    canary = "canary-key-do-not-print"
    monkeypatch.setenv("MISTRAL_API_KEY", canary)
    configured = ContentSettings(_env_file=None)
    assert isinstance(configured.mistral_api_key, SecretStr)
    assert configured.mistral_api_key.get_secret_value() == canary
    assert canary not in str(configured.mistral_api_key)
    assert canary not in repr(configured.mistral_api_key)


def test_retry_delay_prefers_retry_after_and_caps() -> None:
    fresh = ContentSettings(_env_file=None)
    assert fresh.retry_delay(0, retry_after_seconds=5.0) == 5.0
    cap = fresh.retry_max_delay_seconds
    assert fresh.retry_delay(0, retry_after_seconds=9999.0) == cap
    assert fresh.retry_delay(10) == cap


def test_endpoint_requires_https_or_loopback_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The field reads env alias CONTENT_PROVIDER_ENDPOINT, so drive it there.
    monkeypatch.delenv("CONTENT_PROVIDER_ENDPOINT", raising=False)
    # Default (real Mistral endpoint) passes.
    assert ContentSettings(_env_file=None).endpoint.startswith("https://")
    # Explicit https passes; http is loopback-only (local mock servers).
    monkeypatch.setenv("CONTENT_PROVIDER_ENDPOINT", "https://api.example.com/v1")
    assert ContentSettings(_env_file=None).endpoint == "https://api.example.com/v1"
    monkeypatch.setenv("CONTENT_PROVIDER_ENDPOINT", "http://localhost:8080/v1")
    assert ContentSettings(_env_file=None).endpoint == "http://localhost:8080/v1"
    for bad in (
        "http://api.example.com/v1",  # plaintext to a real host
        "ftp://api.example.com/v1",  # non-http scheme
        "not-a-url",  # no host at all
        "",
    ):
        monkeypatch.setenv("CONTENT_PROVIDER_ENDPOINT", bad)
        with pytest.raises(ValidationError):
            ContentSettings(_env_file=None)
