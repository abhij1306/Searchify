"""Unit tests for the project/prompt input normalization (B3).

Ports the reference ``_normalize_prompts`` / ``_normalize_benchmark_mode``
behaviour: intents are casefolded + validated (unknown -> ""), empty-text
prompts are dropped, and benchmark modes are validated against the enum.
"""

from __future__ import annotations

import pytest

from app.core.config.projects import DEFAULT_BENCHMARK_MODE
from app.domain.projects.normalization import (
    normalize_benchmark_mode,
    normalize_intent,
    normalize_prompt_rows,
)


def test_normalize_intent_casefolds_known() -> None:
    assert normalize_intent("Discovery") == "discovery"
    assert normalize_intent("  COMPARISON ") == "comparison"


def test_normalize_intent_drops_unknown() -> None:
    assert normalize_intent("nonsense") == ""
    assert normalize_intent("") == ""
    assert normalize_intent(None) == ""


def test_normalize_benchmark_mode_default_and_validate() -> None:
    assert normalize_benchmark_mode("") == DEFAULT_BENCHMARK_MODE
    assert normalize_benchmark_mode(None) == DEFAULT_BENCHMARK_MODE
    assert normalize_benchmark_mode("Consumer_Like") == "consumer_like"
    assert normalize_benchmark_mode("forced_grounded") == "forced_grounded"


def test_normalize_benchmark_mode_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported benchmark_mode"):
        normalize_benchmark_mode("teleport")


def test_normalize_prompt_rows_trims_and_drops_empty() -> None:
    rows = normalize_prompt_rows(
        [
            {"text": "  best shoes  ", "theme": " footwear ", "intent": "Purchase"},
            {"text": "   ", "intent": "discovery"},
            {"text": "cheap laptops", "intent": "bogus"},
        ]
    )
    assert rows == [
        {"text": "best shoes", "theme": "footwear", "intent": "purchase"},
        {"text": "cheap laptops", "theme": "", "intent": ""},
    ]


def test_normalize_prompt_rows_preserves_extra_fields() -> None:
    rows = normalize_prompt_rows(
        [
            {
                "text": "q",
                "branded": True,
                "enabled": False,
                "origin": "imported",
                "generation_evidence": {"model": "x"},
            }
        ]
    )
    assert rows[0]["branded"] is True
    assert rows[0]["enabled"] is False
    assert rows[0]["origin"] == "imported"
    assert rows[0]["generation_evidence"] == {"model": "x"}
