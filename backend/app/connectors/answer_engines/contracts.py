"""Provider-neutral contracts for answer-engine adapters.

Every field is transport-agnostic so the Gemini (``google``), Anthropic
(``anthropic``) adapters produce the same shape. The response
records the resolved provenance triple — ``logical_engine`` (what was asked
for), ``transport_provider`` (how it was reached), and ``transport_model`` (the
concrete model) — so downstream persistence carries identity per invariant 10.

Ported from the reference ``ai_visibility/contracts.py`` and extended with the
logical/transport provenance fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AnswerEngineRequest:
    prompt: str
    system_instruction: str
    model: str
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class SearchEventResult:
    sequence: int
    query: str
    call_id: str = ""
    call_sequence: int = 0
    query_sequence: int = 0


@dataclass(frozen=True, slots=True)
class CitationResult:
    ordinal: int
    url: str
    title: str
    domain: str
    start_index: int | None
    end_index: int | None
    cited_text: str


@dataclass(frozen=True, slots=True)
class AnswerEngineResponse:
    # Provenance triple (invariant 10): logical engine requested, transport used
    # to reach it, and the concrete transport model that answered.
    logical_engine: str
    transport_provider: str
    transport_model: str
    answer_text: str
    search_used: bool
    search_events: tuple[SearchEventResult, ...]
    citations: tuple[CitationResult, ...]
    provider_metadata: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0
