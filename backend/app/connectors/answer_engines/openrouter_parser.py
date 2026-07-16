"""Parser for OpenRouter's normalized Chat Completions web-search response.

Ported from the reference ``ai_visibility/openrouter_parser.py``; emits the
provenance triple instead of a single ``provider`` string (invariant 10).
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


def _message(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return {}
    message = choices[0].get("message") or {}
    return message if isinstance(message, dict) else {}


def _answer_and_annotations(
    message: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    content = message.get("content")
    if isinstance(content, str):
        return content, list(message.get("annotations") or [])
    texts: list[str] = []
    annotations: list[dict[str, Any]] = list(message.get("annotations") or [])
    for block in content or []:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "").strip()
        if text:
            texts.append(text)
        annotations.extend(
            item
            for item in (block.get("annotations") or [])
            if isinstance(item, dict)
        )
    return "\n\n".join(texts), annotations


def _citations(
    annotations: list[dict[str, Any]], answer_text: str
) -> tuple[CitationResult, ...]:
    results: list[CitationResult] = []
    for annotation in annotations:
        if str(annotation.get("type") or "") != "url_citation":
            continue
        detail = annotation.get("url_citation") or annotation
        url = str(detail.get("url") or "").strip()
        title = str(detail.get("title") or "").strip()
        if not url:
            continue
        start = detail.get("start_index")
        end = detail.get("end_index")
        if not isinstance(start, int) or not isinstance(end, int):
            start = end = None
        cited_text = str(detail.get("content") or "")
        if (
            not cited_text
            and start is not None
            and end is not None
            and 0 <= start < end <= len(answer_text)
        ):
            cited_text = answer_text[start:end]
        domain = normalize_domain(urlparse(url).hostname or title)
        results.append(
            CitationResult(
                ordinal=len(results),
                url=url,
                title=title,
                domain=domain,
                start_index=start,
                end_index=end,
                cited_text=cited_text,
            )
        )
    return tuple(results)


def parse_openrouter_completion(
    payload: dict[str, Any],
    *,
    logical_engine: str,
    transport_provider: str,
    requested_model: str,
    latency_ms: int,
) -> AnswerEngineResponse:
    message = _message(payload)
    answer_text, annotations = _answer_and_annotations(message)
    usage = dict(payload.get("usage") or {})
    # OpenRouter reports the native web-search count under
    # ``server_tool_use_details``; older/other routes use ``server_tool_use``.
    # Accept either so the search-used signal survives a key rename, and fall
    # back to citation presence when neither counter is populated.
    server_tool_use = (
        usage.get("server_tool_use_details")
        or usage.get("server_tool_use")
        or {}
    )
    search_count = int(server_tool_use.get("web_search_requests") or 0)
    normalized_usage = {
        "total_input_tokens": int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        ),
        "total_output_tokens": int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        ),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "web_search_requests": search_count,
        "provider_cost_usd": float(usage.get("cost") or 0),
    }
    citations = _citations(annotations, answer_text)
    # url_citation annotations only exist when native web search actually ran,
    # so treat their presence as proof of search even if the counter is absent.
    search_used = search_count > 0 or bool(citations)
    return AnswerEngineResponse(
        logical_engine=logical_engine,
        transport_provider=transport_provider,
        transport_model=str(payload.get("model") or requested_model),
        answer_text=answer_text,
        search_used=search_used,
        # OpenRouter standardizes count, not provider-generated query strings.
        search_events=tuple(
            SearchEventResult(sequence=index, query="")
            for index in range(search_count)
        ),
        citations=citations,
        provider_metadata={
            "id": payload.get("id"),
            "object": payload.get("object"),
            "model": payload.get("model") or requested_model,
            "routed_provider": payload.get("provider"),
            "usage": normalized_usage,
            "native_search_requested": True,
            "query_text_available": False,
            "finish_reason": (payload.get("choices") or [{}])[0].get(
                "finish_reason"
            ),
            "annotations": annotations,
        },
        usage=normalized_usage,
        latency_ms=latency_ms,
    )
