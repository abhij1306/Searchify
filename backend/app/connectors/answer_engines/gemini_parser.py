"""Tolerant parser for the Gemini Interactions API grounded-search response.

Ported from the reference ``ai_visibility/gemini_parser.py``. Adapted to emit
the provenance triple (``logical_engine`` / ``transport_provider`` /
``transport_model``) instead of a single ``provider`` string (invariant 10).

Observed response shape (grounded):
    {
      "status": "completed",
      "model": "...",
      "usage": {...},
      "steps": [
        {"type": "thought", "signature": "..."},            # dropped
        {"type": "model_output", "content": [
            {"type": "text", "text": "...",
             "annotations": [
               {"type": "url_citation", "url": "<redirect>",
                "title": "<publisher-domain>",
                "start_index": 514, "end_index": 623}
             ]}
        ]},
        {"type": "google_search_call",
         "arguments": {"queries": ["...", "..."]}},
        {"type": "google_search_result", ...}
      ]
    }

Key facts used here:
  * The citation ``url`` is a Google grounding-redirect URL, NOT the publisher
    URL. The publisher domain is carried in ``title``; we derive the citation
    domain from ``title``.
  * A valid answer may contain no ``google_search_call`` step (model answered
    from memory). That is a real benchmark result, not an error.
  * REST vs SDK casing differs; we accept both snake_case and camelCase offsets.
"""

from __future__ import annotations

from typing import Any

from app.connectors.answer_engines.contracts import (
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.connectors.answer_engines.normalization import normalize_domain

_DROP_STEP_TYPES = frozenset({"thought"})


def _step_type(step: dict[str, Any]) -> str:
    return str(step.get("type") or step.get("step_type") or "").strip()


def _extract_queries(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    call_sequence = 0
    for step in steps:
        if _step_type(step) != "google_search_call":
            continue
        args = step.get("arguments") or step.get("args") or {}
        call_id = str(step.get("id") or step.get("call_id") or "")
        for query_sequence, raw in enumerate(args.get("queries") or []):
            text = str(raw or "").strip()
            if text:
                queries.append(
                    {
                        "query": text,
                        "call_id": call_id,
                        "call_sequence": call_sequence,
                        "query_sequence": query_sequence,
                    }
                )
        call_sequence += 1
    return queries


def _text_blocks(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for step in steps:
        if _step_type(step) != "model_output":
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and (
                block.get("type") in (None, "text") or "text" in block
            ):
                blocks.append(block)
    return blocks


def _offset(annotation: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in annotation and annotation[key] is not None:
            try:
                return int(annotation[key])
            except (TypeError, ValueError):
                return None
    return None


def _extract_citations(blocks: list[dict[str, Any]]) -> list[CitationResult]:
    citations: list[CitationResult] = []
    ordinal = 0
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
            start = _offset(annotation, "start_index", "startIndex")
            end = _offset(annotation, "end_index", "endIndex")
            # Derive cited text from the answer where offsets are valid, rather
            # than trusting a possibly-stale provider-duplicated field.
            cited_text = ""
            if (
                start is not None
                and end is not None
                and 0 <= start < end <= len(text)
            ):
                cited_text = text[start:end]
            else:
                start = None
                end = None
            citations.append(
                CitationResult(
                    ordinal=ordinal,
                    url=url,
                    title=title,
                    domain=normalize_domain(title),
                    start_index=start,
                    end_index=end,
                    cited_text=cited_text,
                )
            )
            ordinal += 1
    return citations


def sanitize_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep observable, non-sensitive provider fields only.

    Retains status/model/usage/object and a redacted evidence envelope
    containing search-call grouping, model output, and citation annotations.
    Strips thought steps/signatures and never carries credentials.
    """
    steps = [
        step
        for step in (payload.get("steps") or [])
        if isinstance(step, dict) and _step_type(step) not in _DROP_STEP_TYPES
    ]
    step_types = [_step_type(step) for step in steps]
    evidence_steps: list[dict[str, Any]] = []
    for step in steps:
        step_type = _step_type(step)
        common = {
            "type": step_type,
            "id": step.get("id"),
            "call_id": step.get("call_id") or step.get("callId"),
        }
        if step_type == "google_search_call":
            arguments = step.get("arguments") or step.get("args") or {}
            evidence_steps.append(
                {
                    **common,
                    "arguments": {"queries": arguments.get("queries") or []},
                }
            )
        elif step_type == "google_search_result":
            evidence_steps.append(common)
        elif step_type == "model_output":
            content = []
            for block in step.get("content") or []:
                if not isinstance(block, dict):
                    continue
                content.append(
                    {
                        "type": block.get("type"),
                        "text": block.get("text"),
                        "annotations": block.get("annotations") or [],
                    }
                )
            evidence_steps.append({**common, "content": content})
    return {
        "interaction_id": payload.get("id"),
        "status": payload.get("status"),
        "model": payload.get("model"),
        "object": payload.get("object"),
        "usage": payload.get("usage") or {},
        "step_types": step_types,
        "evidence_steps": evidence_steps,
    }


def parse_interaction(
    payload: dict[str, Any],
    *,
    logical_engine: str,
    transport_provider: str,
    model: str,
    latency_ms: int,
) -> AnswerEngineResponse:
    steps_raw = payload.get("steps") or []
    steps = [
        step
        for step in steps_raw
        if isinstance(step, dict) and _step_type(step) not in _DROP_STEP_TYPES
    ]

    queries = _extract_queries(steps)
    blocks = _text_blocks(steps)
    answer_text = "\n\n".join(
        str(block.get("text") or "").strip()
        for block in blocks
        if str(block.get("text") or "").strip()
    )
    citations = _extract_citations(blocks)

    search_used = any(
        _step_type(step) == "google_search_call" for step in steps
    )

    return AnswerEngineResponse(
        logical_engine=logical_engine,
        transport_provider=transport_provider,
        transport_model=str(payload.get("model") or model),
        answer_text=answer_text,
        search_used=search_used,
        search_events=tuple(
            SearchEventResult(sequence=index, **query)
            for index, query in enumerate(queries)
        ),
        citations=tuple(citations),
        provider_metadata=sanitize_metadata(payload),
        usage=dict(payload.get("usage") or {}),
        latency_ms=latency_ms,
    )
