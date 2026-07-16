"""Answer-engine adapters (BYOK, transport-agnostic, invariant 6/10).

Each adapter turns an ``AnswerEngineRequest`` into a normalized
``AnswerEngineResponse`` carrying the provenance triple (logical_engine /
transport_provider / transport_model). MVP transports: ``google`` (Gemini
direct), ``anthropic`` (Claude direct), and ``openrouter`` (chatgpt/claude/
gemini). A direct OpenAI adapter is a reserved fast-follow, NOT in MVP.
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
from app.connectors.answer_engines.openrouter import (
    OpenRouterAnswerEngineAdapter,
)

__all__ = [
    "AnswerEngineRequest",
    "AnswerEngineResponse",
    "AnthropicAnswerEngineAdapter",
    "CitationResult",
    "GeminiAnswerEngineAdapter",
    "OpenRouterAnswerEngineAdapter",
    "ProviderError",
    "SearchEventResult",
    "build_adapter",
    "classify_provider_status",
]
