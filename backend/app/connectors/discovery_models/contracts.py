"""Provider-neutral contracts for discovery-model (content) clients.

A ``DiscoveryModelClient`` takes chat ``messages`` and returns generated text
with provenance (provider, requested vs returned model, finish reason, usage,
latency). Every field is transport-agnostic so a provider swap is a factory
branch, never a domain/API change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    """One generation request: chat messages + the resolved model + caps."""

    messages: tuple[dict, ...]
    model: str
    timeout_seconds: float
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class DiscoveryResponse:
    """One successful generation with full model provenance."""

    provider: str
    requested_model: str
    returned_model: str
    output_text: str
    finish_reason: str
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0


@runtime_checkable
class DiscoveryModelClient(Protocol):
    """The provider-neutral content-generation client contract."""

    provider: str

    async def generate(self, request: DiscoveryRequest) -> DiscoveryResponse:
        """Run one generation. Raises ``ProviderError`` on failure."""
        ...
