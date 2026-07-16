"""Answer-engine adapter unit tests for ALL MVP engines.

Covers the B4 adapter acceptance: Gemini direct (``google`` transport, grounding
+ citation parsing), Claude direct (``anthropic``), and OpenRouter (chatgpt +
claude). Adapted from the reference
``tests/unit/test_{anthropic,openrouter}_ai_visibility.py`` plus new
Gemini-direct coverage. Each parser assertion checks the recorded provenance
triple — ``logical_engine`` + ``transport_provider`` + ``transport_model``
(invariant 10). HTTP transports are mocked; no real API spend.
"""

from __future__ import annotations

import httpx
import pytest

from app.connectors.answer_engines.anthropic import (
    AnthropicAnswerEngineAdapter,
    _raise_for_search_error,
)
from app.connectors.answer_engines.anthropic import (
    _payload as anthropic_payload,
)
from app.connectors.answer_engines.anthropic_parser import (
    parse_anthropic_message,
)
from app.connectors.answer_engines.contracts import AnswerEngineRequest
from app.connectors.answer_engines.errors import ProviderError
from app.connectors.answer_engines.gemini import GeminiAnswerEngineAdapter
from app.connectors.answer_engines.gemini_parser import parse_interaction
from app.connectors.answer_engines.openrouter import (
    OpenRouterAnswerEngineAdapter,
    _model_matches_surface,
)
from app.connectors.answer_engines.openrouter import (
    _payload as openrouter_payload,
)
from app.connectors.answer_engines.openrouter_parser import (
    parse_openrouter_completion,
)


def _mock_transport(payload: dict, status_code: int = 200) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Gemini direct (google transport) — NEW coverage
# ---------------------------------------------------------------------------
_GEMINI_GROUNDED = {
    "id": "int_1",
    "status": "completed",
    "model": "gemini-flash-latest",
    "usage": {"total_tokens": 120},
    "steps": [
        {"type": "thought", "signature": "drop-me"},
        {
            "type": "google_search_call",
            "id": "call_1",
            "arguments": {"queries": ["best running shoes australia"]},
        },
        {"type": "google_search_result"},
        {
            "type": "model_output",
            "content": [
                {
                    "type": "text",
                    "text": "Brooks is a strong pick for road running.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url": "https://grounding-api-redirect/xyz",
                            "title": "runnersworld.com",
                            "start_index": 0,
                            "end_index": 6,
                        }
                    ],
                }
            ],
        },
    ],
}


def test_gemini_parser_grounding_citations_and_provenance() -> None:
    result = parse_interaction(
        _GEMINI_GROUNDED,
        logical_engine="gemini",
        transport_provider="google",
        model="gemini-flash-latest",
        latency_ms=42,
    )
    # Provenance triple (invariant 10).
    assert result.logical_engine == "gemini"
    assert result.transport_provider == "google"
    assert result.transport_model == "gemini-flash-latest"
    # Grounding + citation parsing.
    assert result.search_used is True
    assert len(result.search_events) == 1
    assert result.search_events[0].query == "best running shoes australia"
    assert len(result.citations) == 1
    citation = result.citations[0]
    # Domain is derived from the title (the url is a redirect).
    assert citation.domain == "runnersworld.com"
    assert citation.cited_text == "Brooks"
    # Thought steps are dropped from the answer text.
    assert result.answer_text == "Brooks is a strong pick for road running."


def test_gemini_parser_no_search_is_valid_result() -> None:
    payload = {
        "model": "gemini-flash-latest",
        "steps": [
            {
                "type": "model_output",
                "content": [{"type": "text", "text": "From memory: A, B."}],
            }
        ],
    }
    result = parse_interaction(
        payload,
        logical_engine="gemini",
        transport_provider="google",
        model="gemini-flash-latest",
        latency_ms=1,
    )
    assert result.search_used is False
    assert result.citations == ()
    assert result.answer_text == "From memory: A, B."


async def test_gemini_adapter_executes_and_records_provenance() -> None:
    adapter = GeminiAnswerEngineAdapter(api_key="secret-google-key")
    transport = _mock_transport(_GEMINI_GROUNDED)
    request = AnswerEngineRequest(
        prompt="running shoes",
        system_instruction="",
        model="gemini-flash-latest",
        timeout_seconds=5,
    )
    # Patch the module-level client construction to use the mock transport.
    import app.connectors.answer_engines.gemini as gemini_mod

    orig = httpx.AsyncClient

    def _client(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    gemini_mod.httpx.AsyncClient = _client  # type: ignore[assignment]
    try:
        result = await adapter.execute(request)
    finally:
        gemini_mod.httpx.AsyncClient = orig  # type: ignore[assignment]
    assert result.transport_provider == "google"
    assert result.logical_engine == "gemini"
    assert result.search_used is True


async def test_gemini_adapter_maps_http_error() -> None:
    adapter = GeminiAnswerEngineAdapter(api_key="k")
    transport = _mock_transport({"error": {"status": "RESOURCE_EXHAUSTED"}}, 429)
    import app.connectors.answer_engines.gemini as gemini_mod

    orig = httpx.AsyncClient

    def _client(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    gemini_mod.httpx.AsyncClient = _client  # type: ignore[assignment]
    try:
        with pytest.raises(ProviderError) as excinfo:
            await adapter.execute(
                AnswerEngineRequest(
                    prompt="x",
                    system_instruction="",
                    model="gemini-flash-latest",
                    timeout_seconds=5,
                )
            )
    finally:
        gemini_mod.httpx.AsyncClient = orig  # type: ignore[assignment]
    assert excinfo.value.error_code == "rate_limit"
    assert excinfo.value.retryable is True


def test_gemini_adapter_requires_key() -> None:
    with pytest.raises(ProviderError) as excinfo:
        GeminiAnswerEngineAdapter(api_key="")
    assert excinfo.value.error_code == "auth_failure"


# ---------------------------------------------------------------------------
# Claude direct (anthropic transport)
# ---------------------------------------------------------------------------
def test_anthropic_payload_uses_native_web_search_and_top_level_system() -> None:
    request = AnswerEngineRequest(
        prompt="cheap baby clothes",
        system_instruction="Answer for Australia.",
        model="claude-sonnet-4-6",
        timeout_seconds=30,
    )
    payload = anthropic_payload(request, country_code="AU")
    assert payload["system"] == "Answer for Australia."
    assert payload["messages"] == [
        {"role": "user", "content": "cheap baby clothes"}
    ]
    tool = payload["tools"][0]
    assert tool["type"] == "web_search_20250305"
    assert tool["name"] == "web_search"
    assert tool["user_location"] == {"type": "approximate", "country": "AU"}


def test_anthropic_payload_omits_system_and_location_when_absent() -> None:
    request = AnswerEngineRequest(
        prompt="school uniforms",
        system_instruction="",
        model="claude-sonnet-4-6",
        timeout_seconds=30,
    )
    payload = anthropic_payload(request, country_code="")
    assert "system" not in payload
    assert "user_location" not in payload["tools"][0]


def test_anthropic_parser_extracts_answer_citations_and_provenance() -> None:
    payload = {
        "id": "msg_1",
        "type": "message",
        "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": "Let me search."},
            {
                "type": "server_tool_use",
                "id": "srvtoolu_1",
                "name": "web_search",
                "input": {"query": "affordable baby clothes australia"},
            },
            {
                "type": "text",
                "text": "Best&Less is a great option.",
                "citations": [
                    {
                        "type": "web_search_result_location",
                        "url": "https://www.bestandless.com.au/baby",
                        "title": "Best&Less baby",
                        "cited_text": "Best&Less baby clothing from $5",
                    }
                ],
            },
        ],
        "usage": {
            "input_tokens": 40,
            "output_tokens": 60,
            "server_tool_use": {"web_search_requests": 1},
        },
    }
    result = parse_anthropic_message(
        payload,
        logical_engine="claude",
        transport_provider="anthropic",
        requested_model="claude-sonnet-4-6",
        latency_ms=12,
    )
    assert result.logical_engine == "claude"
    assert result.transport_provider == "anthropic"
    assert result.transport_model == "claude-sonnet-4-6"
    assert result.answer_text == "Let me search.\n\nBest&Less is a great option."
    assert result.search_used is True
    assert result.search_events[0].query == "affordable baby clothes australia"
    assert result.provider_metadata["query_text_available"] is True
    assert result.citations[0].domain == "bestandless.com.au"
    assert result.citations[0].cited_text == "Best&Less baby clothing from $5"
    assert result.usage["total_tokens"] == 100
    assert result.usage["web_search_requests"] == 1


def test_anthropic_search_error_raises_only_for_retryable_codes() -> None:
    rate_limited = {
        "content": [
            {
                "type": "web_search_tool_result",
                "content": {
                    "type": "web_search_tool_result_error",
                    "error_code": "too_many_requests",
                },
            }
        ]
    }
    with pytest.raises(ProviderError) as excinfo:
        _raise_for_search_error(rate_limited)
    assert excinfo.value.retryable is True

    capped = {
        "content": [
            {
                "type": "web_search_tool_result",
                "content": {
                    "type": "web_search_tool_result_error",
                    "error_code": "max_uses_exceeded",
                },
            }
        ]
    }
    _raise_for_search_error(capped)  # must not raise


async def test_anthropic_adapter_executes_and_records_provenance() -> None:
    payload = {
        "id": "msg_2",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    transport = _mock_transport(payload)
    adapter = AnthropicAnswerEngineAdapter(api_key="secret-anthropic-key")

    import app.connectors.answer_engines.anthropic as anthropic_mod

    orig = httpx.AsyncClient

    def _client(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    anthropic_mod.httpx.AsyncClient = _client  # type: ignore[assignment]
    try:
        result = await adapter.execute(
            AnswerEngineRequest(
                prompt="x",
                system_instruction="",
                model="claude-sonnet-4-6",
                timeout_seconds=5,
            )
        )
    finally:
        anthropic_mod.httpx.AsyncClient = orig  # type: ignore[assignment]
    assert result.transport_provider == "anthropic"
    assert result.logical_engine == "claude"
    assert result.transport_model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# OpenRouter (chatgpt + claude transports)
# ---------------------------------------------------------------------------
def test_openrouter_payload_requests_native_search_and_country() -> None:
    request = AnswerEngineRequest(
        prompt="cheap baby clothes",
        system_instruction="Answer for Australia.",
        model="openai/gpt-5.4",
        timeout_seconds=30,
    )
    payload = openrouter_payload(request, country_code="AU")
    assert payload["messages"] == [
        {"role": "system", "content": "Answer for Australia."},
        {"role": "user", "content": "cheap baby clothes"},
    ]
    tool = payload["tools"][0]
    assert tool["type"] == "openrouter:web_search"
    assert tool["parameters"]["engine"] == "native"
    assert tool["parameters"]["user_location"]["country"] == "AU"


@pytest.mark.parametrize(
    ("logical_engine", "model", "expected"),
    [
        ("chatgpt", "openai/gpt-5.4", True),
        ("chatgpt", "openai/gpt-4o", False),
        ("claude", "anthropic/claude-sonnet-4.6", True),
        ("claude", "openai/gpt-5.4", False),
        ("gemini", "google/gemini-2.5-flash", True),
    ],
)
def test_openrouter_surface_model_allowlist(
    logical_engine: str, model: str, expected: bool
) -> None:
    assert _model_matches_surface(logical_engine, model) is expected


def test_openrouter_parser_chatgpt_provenance_and_citations() -> None:
    payload = {
        "id": "gen-1",
        "model": "openai/gpt-5.4",
        "provider": "OpenAI",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "Best&Less is one option.",
                    "annotations": [
                        {
                            "type": "url_citation",
                            "url_citation": {
                                "url": "https://www.bestandless.com.au/baby",
                                "title": "Best&Less baby clothing",
                                "start_index": 0,
                                "end_index": 9,
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 30,
            "total_tokens": 50,
            "server_tool_use": {"web_search_requests": 2},
        },
    }
    result = parse_openrouter_completion(
        payload,
        logical_engine="chatgpt",
        transport_provider="openrouter",
        requested_model="openai/gpt-5.4",
        latency_ms=10,
    )
    # chatgpt reaches MVP via OpenRouter only (decision B-3).
    assert result.logical_engine == "chatgpt"
    assert result.transport_provider == "openrouter"
    assert result.transport_model == "openai/gpt-5.4"
    assert result.search_used is True
    assert len(result.search_events) == 2
    assert result.citations[0].domain == "bestandless.com.au"
    assert result.provider_metadata["routed_provider"] == "OpenAI"


def test_openrouter_parser_claude_provenance() -> None:
    payload = {
        "id": "gen-2",
        "model": "anthropic/claude-sonnet-4.6",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Answer."},
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
    }
    result = parse_openrouter_completion(
        payload,
        logical_engine="claude",
        transport_provider="openrouter",
        requested_model="anthropic/claude-sonnet-4.6",
        latency_ms=3,
    )
    assert result.logical_engine == "claude"
    assert result.transport_provider == "openrouter"
    assert result.transport_model == "anthropic/claude-sonnet-4.6"


async def test_openrouter_adapter_rejects_off_surface_model() -> None:
    adapter = OpenRouterAnswerEngineAdapter(
        api_key="k", logical_engine="chatgpt"
    )
    with pytest.raises(ProviderError) as excinfo:
        await adapter.execute(
            AnswerEngineRequest(
                prompt="x",
                system_instruction="",
                model="openai/gpt-4o",  # off the approved surface
                timeout_seconds=5,
            )
        )
    assert excinfo.value.error_code == "invalid_surface"


def test_openrouter_adapter_rejects_unknown_engine() -> None:
    with pytest.raises(ValueError):
        OpenRouterAnswerEngineAdapter(api_key="k", logical_engine="bogus")


async def test_openrouter_adapter_executes_and_records_provenance() -> None:
    payload = {
        "id": "gen-3",
        "model": "openai/gpt-5.4",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "ok"},
            }
        ],
        "usage": {"total_tokens": 2},
    }
    transport = _mock_transport(payload)
    adapter = OpenRouterAnswerEngineAdapter(
        api_key="secret-openrouter-key", logical_engine="chatgpt"
    )
    import app.connectors.answer_engines.openrouter as openrouter_mod

    orig = httpx.AsyncClient

    def _client(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs["transport"] = transport
        return orig(*args, **kwargs)

    openrouter_mod.httpx.AsyncClient = _client  # type: ignore[assignment]
    try:
        result = await adapter.execute(
            AnswerEngineRequest(
                prompt="x",
                system_instruction="",
                model="openai/gpt-5.4",
                timeout_seconds=5,
            )
        )
    finally:
        openrouter_mod.httpx.AsyncClient = orig  # type: ignore[assignment]
    assert result.logical_engine == "chatgpt"
    assert result.transport_provider == "openrouter"
