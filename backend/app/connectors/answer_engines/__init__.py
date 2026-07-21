"""Answer-engine adapters (BYOK, transport-agnostic, invariant 6/10).

Each adapter turns an ``AnswerEngineRequest`` into a normalized
``AnswerEngineResponse`` carrying the provenance triple (logical_engine /
transport_provider / transport_model). The only active transports are
``openai`` (ChatGPT direct), ``anthropic`` (Claude direct), and ``google``
(Gemini direct).
"""

from __future__ import annotations

from app.connectors.answer_engines.anthropic import AnthropicAnswerEngineAdapter
from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.connectors.answer_engines.errors import (
    ProviderError,
    classify_provider_status,
)
from app.connectors.answer_engines.factory import build_adapter
from app.connectors.answer_engines.gemini import GeminiAnswerEngineAdapter
from app.connectors.answer_engines.openai import OpenAIAnswerEngineAdapter

__all__ = [
    "AnswerEngineRequest",
    "AnswerEngineResponse",
    "AnthropicAnswerEngineAdapter",
    "CitationResult",
    "GeminiAnswerEngineAdapter",
    "OpenAIAnswerEngineAdapter",
    "ProviderError",
    "SearchEventResult",
    "build_adapter",
    "classify_provider_status",
]
