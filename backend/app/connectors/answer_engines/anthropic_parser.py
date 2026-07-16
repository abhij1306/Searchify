"""Parser for Anthropic Messages API web-search responses.

Ported from the reference ``ai_visibility/anthropic_parser.py``; emits the
provenance triple instead of a single ``provider`` string (invariant 10).

Anthropic returns a block list rather than a single message string:

  - ``text`` blocks carry the answer, each with an optional ``citations`` list
    of ``web_search_result_location`` entries (url/title/cited_text).
  - ``server_tool_use`` blocks (``name == "web_search"``) carry the actual
    search query text — unlike OpenRouter, which only reports a count.
  - ``usage.server_tool_use.web_search_requests`` counts the searches performed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.connectors.answer_engines.contracts import (
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.connectors.answer_engines.normalization import normalize_domain


def _blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    content = payload.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _answer_and_citations(
    blocks: list[dict[str, Any]],
) -> tuple[str, tuple[CitationResult, ...]]:
    texts: list[str] = []
    citations: list[CitationResult] = []
    for block in blocks:
        if str(block.get("type") or "") != "text":
            continue
        text = str(block.get("text") or "").strip()
        if text:
            texts.append(text)
        for annotation in block.get("citations") or []:
            if not isinstance(annotation, dict):
                continue
            if str(annotation.get("type") or "") != "web_search_result_location":
                continue
            url = str(annotation.get("url") or "").strip()
            if not url:
                continue
            title = str(annotation.get("title") or "").strip()
            cited_text = str(annotation.get("cited_text") or "")
            domain = normalize_domain(urlparse(url).hostname or title)
            citations.append(
                CitationResult(
                    ordinal=len(citations),
                    url=url,
                    title=title,
                    domain=domain,
                    # Anthropic returns cited_text, not character offsets.
                    start_index=None,
                    end_index=None,
                    cited_text=cited_text,
                )
            )
    return "\n\n".join(texts), tuple(citations)


def _search_events(
    blocks: list[dict[str, Any]],
) -> tuple[SearchEventResult, ...]:
    events: list[SearchEventResult] = []
    for block in blocks:
        if str(block.get("type") or "") != "server_tool_use":
            continue
        if str(block.get("name") or "") != "web_search":
            continue
        query = str((block.get("input") or {}).get("query") or "")
        events.append(SearchEventResult(sequence=len(events), query=query))
    return tuple(events)


def parse_anthropic_message(
    payload: dict[str, Any],
    *,
    logical_engine: str,
    transport_provider: str,
    requested_model: str,
    latency_ms: int,
) -> AnswerEngineResponse:
    blocks = _blocks(payload)
    answer_text, citations = _answer_and_citations(blocks)
    search_events = _search_events(blocks)

    usage = dict(payload.get("usage") or {})
    server_tool_use = usage.get("server_tool_use") or {}
    search_count = int(server_tool_use.get("web_search_requests") or 0)
    # Prefer the reported count; fall back to observed server_tool_use blocks.
    if not search_count:
        search_count = len(search_events)
    normalized_usage = {
        "total_input_tokens": int(usage.get("input_tokens") or 0),
        "total_output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(
            (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        ),
        "web_search_requests": search_count,
        # Anthropic does not return a per-request dollar cost.
        "provider_cost_usd": 0.0,
    }
    return AnswerEngineResponse(
        logical_engine=logical_engine,
        transport_provider=transport_provider,
        transport_model=str(payload.get("model") or requested_model),
        answer_text=answer_text,
        search_used=search_count > 0,
        search_events=search_events,
        citations=citations,
        provider_metadata={
            "id": payload.get("id"),
            "type": payload.get("type"),
            "model": payload.get("model") or requested_model,
            "usage": normalized_usage,
            "native_search_requested": True,
            # Anthropic exposes the real query text on server_tool_use blocks.
            "query_text_available": True,
            "stop_reason": payload.get("stop_reason"),
        },
        usage=normalized_usage,
        latency_ms=latency_ms,
    )
