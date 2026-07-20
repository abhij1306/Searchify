"""Mistral discovery client + factory tests (mock transport, no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.connectors.answer_engines.errors import ProviderError
from app.connectors.discovery_models.contracts import DiscoveryRequest
from app.connectors.discovery_models.factory import build_discovery_client
from app.connectors.discovery_models.mistral import MistralDiscoveryClient
from app.core.config.content import content_settings
from app.core.config.provider_catalog import (
    ERROR_AUTH,
    ERROR_INVALID_SURFACE,
    ERROR_PARSE,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
)

_ENDPOINT = "https://mock.mistral.test/v1/chat/completions"


def _request() -> DiscoveryRequest:
    return DiscoveryRequest(
        messages=(
            {"role": "system", "content": "s"},
            {"role": "user", "content": "write a page"},
        ),
        model="mistral-small-latest",
        timeout_seconds=10.0,
        max_output_tokens=512,
    )


def _client(handler) -> MistralDiscoveryClient:
    return MistralDiscoveryClient(
        api_key="test-key",
        endpoint=_ENDPOINT,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_success_parses_model_finish_reason_usage() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "mistral-small-2409",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "# Hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    response = await _client(handler).generate(_request())
    assert response.provider == "mistral"
    assert response.requested_model == "mistral-small-latest"
    assert response.returned_model == "mistral-small-2409"
    assert response.output_text == "# Hello"
    assert response.finish_reason == "stop"
    assert response.usage["total_tokens"] == 15
    assert response.latency_ms >= 0
    # The key rides only in the Authorization header, never the body.
    assert captured["auth"] == "Bearer test-key"
    assert "test-key" not in json.dumps(captured["body"])
    assert captured["body"]["stream"] is False
    assert captured["body"]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_http_error_classification() -> None:
    def handler_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "7"})

    with pytest.raises(ProviderError) as excinfo:
        await _client(handler_429).generate(_request())
    assert excinfo.value.error_code == ERROR_RATE_LIMIT
    assert excinfo.value.retryable
    assert excinfo.value.retry_after_seconds == 7.0

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(ProviderError) as excinfo:
        await _client(handler_500).generate(_request())
    assert excinfo.value.error_code == ERROR_SERVER
    assert excinfo.value.retryable

    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(ProviderError) as excinfo:
        await _client(handler_401).generate(_request())
    assert excinfo.value.error_code == ERROR_AUTH
    assert not excinfo.value.retryable


@pytest.mark.asyncio
async def test_non_json_and_malformed_body_are_parse_errors() -> None:
    def handler_text(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(ProviderError) as excinfo:
        await _client(handler_text).generate(_request())
    assert excinfo.value.error_code == ERROR_PARSE

    def handler_missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(ProviderError) as excinfo:
        await _client(handler_missing).generate(_request())
    assert excinfo.value.error_code == ERROR_PARSE


@pytest.mark.asyncio
async def test_error_messages_never_contain_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(ProviderError) as excinfo:
        await _client(handler).generate(_request())
    assert "test-key" not in str(excinfo.value)


def test_missing_key_is_auth_error() -> None:
    with pytest.raises(ProviderError) as excinfo:
        MistralDiscoveryClient(api_key="", endpoint=_ENDPOINT)
    assert excinfo.value.error_code == ERROR_AUTH


def test_factory_builds_mistral_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    monkeypatch.setattr(content_settings, "provider", "mistral")
    monkeypatch.setattr(content_settings, "mistral_api_key", SecretStr("k"))
    client = build_discovery_client()
    assert isinstance(client, MistralDiscoveryClient)

    monkeypatch.setattr(content_settings, "provider", "nonexistent")
    with pytest.raises(ProviderError) as excinfo:
        build_discovery_client()
    assert excinfo.value.error_code == ERROR_INVALID_SURFACE
