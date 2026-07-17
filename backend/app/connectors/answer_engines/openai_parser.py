"""Parser for the OpenAI Responses API web-search response.

Direct OpenAI (transport ``openai``) serves the ``chatgpt`` logical engine via
the Responses API with the built-in ``web_search`` tool. Emits the provenance
triple (``logical_engine`` / ``transport_provider`` / ``transport_model``)
instead of a single ``provider`` string (invariant 10).

Observed response shape (grounded):
    {
      "id": "resp_1",
      "object": "response",
      "status": "completed",
      "model": "gpt-5.4",
      "output": [
        {"type": "reasoning", "id": "rs_1", "summary": [...]},   # dropped
        {"type": "web_search_call", "id": "ws_1", "status": "completed",
         "action": {"type": "search", "query": "best running shoes"}},
        {"type": "message", "id": "msg_1", "role": "assistant",
         "content": [
            {"type": "output_text", "text": "...",
             "annotations": [
               {"type": "url_citation", "url": "https://publisher/x",
                "title": "Publisher", "start_index": 0, "end_index": 6}
             ]}
         ]}
      ],
      "usage": {"input_tokens": 40, "output_tokens": 60, "total_tokens": 100}
    }

Key facts used here:
  * The final answer lives in ``message`` items whose ``content`` blocks are
    ``output_text`` with inline ``url_citation`` annotations carrying the real
    publisher URL and character offsets.
  * ``web_search_call`` items carry the provider-generated query text under
    ``action.query`` (single) or ``action.queries`` (multiple). A call with no
    query text is preserved as a count-only empty-query event — never invented.
  * A valid answer may contain NO ``web_search_call`` item (model answered from
    memory). That is a real result, not an error. ``search_used`` is true when
    a search-call item OR a grounded citation proves search occurred.
  * ``reasoning`` items are never retained (no reasoning content, no secrets).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.connectors.answer_engines.contracts import (
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.connectors.answer_engines.normalization import (
    annotation_offset,
    coerce_int,
    normalize_domain,
)

# Output item types we never carry into sanitized metadata (could echo the
# model's private chain-of-thought / secrets).
_DROP_ITEM_TYPES = frozenset({"reasoning"})


def _item_type(item: dict[str, Any]) -> str:
    return str(item.get("type") or "").strip()


def _output_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    return [item for item in output if isinstance(item, dict)]


def _action_queries(action: dict[str, Any]) -> list[str]:
    """Ordered, non-blank query strings from a web_search_call action.

    Accepts a single ``query`` string and/or a ``queries`` list; preserves
    order and never fabricates text.
    """
    queries: list[str] = []
    single = str(action.get("query") or "").strip()
    if single:
        queries.append(single)
    raw_queries = action.get("queries")
    if isinstance(raw_queries, (list, tuple)):
        for raw in raw_queries:
            text = str(raw or "").strip()
            if text:
                queries.append(text)
    return queries


def _search_events(
    items: list[dict[str, Any]],
) -> tuple[tuple[SearchEventResult, ...], int]:
    """Ordered search events + the count of web_search_call items.

    Each provider query becomes a ``SearchEventResult``. A call that carries no
    query text is preserved as a single count-only empty-query event rather
    than being dropped or invented.
    """
    events: list[SearchEventResult] = []
    call_count = 0
    sequence = 0
    for item in items:
        if _item_type(item) != "web_search_call":
            continue
        call_id = str(item.get("id") or item.get("call_id") or "")
        action = item.get("action")
        action = action if isinstance(action, dict) else {}
        queries = _action_queries(action)
        if queries:
            for query_sequence, text in enumerate(queries):
                events.append(
                    SearchEventResult(
                        sequence=sequence,
                        query=text,
                        call_id=call_id,
                        call_sequence=call_count,
                        query_sequence=query_sequence,
                    )
                )
                sequence += 1
        else:
            # Count-only: a search happened but the query text is unavailable.
            events.append(
                SearchEventResult(
                    sequence=sequence,
                    query="",
                    call_id=call_id,
                    call_sequence=call_count,
                    query_sequence=0,
                )
            )
            sequence += 1
        call_count += 1
    return tuple(events), call_count


def _text_blocks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for item in items:
        if _item_type(item) != "message":
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type in ("output_text", "text", "") or "text" in block:
                blocks.append(block)
    return blocks


def _citations(blocks: list[dict[str, Any]]) -> tuple[CitationResult, ...]:
    citations: list[CitationResult] = []
    for block in blocks:
        text = str(block.get("text") or "")
        for annotation in block.get("annotations") or []:
            if not isinstance(annotation, dict):
                continue
            if str(annotation.get("type") or "") != "url_citation":
                continue
            url = str(annotation.get("url") or "").strip()
            title = str(annotation.get("title") or "").strip()
            if not url and not title:
                continue
            start = annotation_offset(annotation, "start_index", "startIndex")
            end = annotation_offset(annotation, "end_index", "endIndex")
            cited_text = ""
            if start is not None and end is not None and 0 <= start < end <= len(text):
                cited_text = text[start:end]
            else:
                start = None
                end = None
            domain = normalize_domain(urlparse(url).hostname or title)
            citations.append(
                CitationResult(
                    ordinal=len(citations),
                    url=url,
                    title=title,
                    domain=domain,
                    start_index=start,
                    end_index=end,
                    cited_text=cited_text,
                )
            )
    return tuple(citations)


def _sanitize_metadata(
    payload: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Keep observable, non-sensitive provider fields only.

    Retains id/object/status/model/usage and a redacted evidence envelope of
    search-call actions + message text/annotations. Reasoning items are dropped
    entirely and no credentials, raw headers, or request echo are retained.
    """
    item_types = [_item_type(item) for item in items]
    evidence_items: list[dict[str, Any]] = []
    for item in items:
        item_type = _item_type(item)
        if item_type in _DROP_ITEM_TYPES:
            continue
        common = {"type": item_type, "id": item.get("id")}
        if item_type == "web_search_call":
            action = item.get("action")
            action = action if isinstance(action, dict) else {}
            evidence_items.append(
                {
                    **common,
                    "status": item.get("status"),
                    "action": {
                        "type": action.get("type"),
                        "query": action.get("query"),
                        "queries": action.get("queries") or [],
                    },
                }
            )
        elif item_type == "message":
            content = []
            for block in item.get("content") or []:
                if not isinstance(block, dict):
                    continue
                content.append(
                    {
                        "type": block.get("type"),
                        "text": block.get("text"),
                        "annotations": block.get("annotations") or [],
                    }
                )
            evidence_items.append({**common, "content": content})
    return {
        "id": payload.get("id"),
        "object": payload.get("object"),
        "status": payload.get("status"),
        "model": payload.get("model"),
        "usage": _normalized_usage(payload),
        "native_search_requested": True,
        "query_text_available": any(
            _item_type(item) == "web_search_call"
            and _action_queries(
                item.get("action") if isinstance(item.get("action"), dict) else {}
            )
            for item in items
        ),
        "item_types": item_types,
        "evidence_items": evidence_items,
    }


def _normalized_usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = coerce_int(usage.get("input_tokens"))
    output_tokens = coerce_int(usage.get("output_tokens"))
    total_tokens = coerce_int(usage.get("total_tokens"), input_tokens + output_tokens)
    return {
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
        "total_tokens": total_tokens,
        # OpenAI does not return a per-request dollar cost.
        "provider_cost_usd": 0.0,
    }


def parse_openai_response(
    payload: dict[str, Any],
    *,
    logical_engine: str,
    transport_provider: str,
    requested_model: str,
    latency_ms: int,
) -> AnswerEngineResponse:
    items = _output_items(payload)

    events, call_count = _search_events(items)
    blocks = _text_blocks(items)
    answer_text = "\n\n".join(
        str(block.get("text") or "").strip()
        for block in blocks
        if str(block.get("text") or "").strip()
    )
    citations = _citations(blocks)

    # Search is proven by a search-call item or a grounded citation.
    search_used = call_count > 0 or bool(citations)

    normalized_usage = _normalized_usage(payload)
    normalized_usage["web_search_requests"] = call_count

    return AnswerEngineResponse(
        # Preserve chatgpt/openai/gpt-5.4 provenance; use the provider-returned
        # model only when present.
        logical_engine=logical_engine,
        transport_provider=transport_provider,
        transport_model=str(payload.get("model") or requested_model),
        answer_text=answer_text,
        search_used=search_used,
        search_events=events,
        citations=citations,
        provider_metadata=_sanitize_metadata(payload, items),
        usage=normalized_usage,
        latency_ms=latency_ms,
    )
