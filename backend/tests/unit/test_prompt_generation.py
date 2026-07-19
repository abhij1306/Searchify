"""Unit tests for the prompt-generation builder/parser + dedupe normalization.

Deterministic fixtures only — no live provider calls (roadmap build-order
rule: unit-test the parser/dedupe against fixture model output).
"""

from __future__ import annotations

import json

import pytest

from app.domain.prompts.generation import (
    GenerationOutputError,
    build_generation_user_message,
    parse_generation_output,
)
from app.domain.prompts.normalization import normalize_prompt_text, prompt_text_hash

BRAND_CONTEXT = {
    "brand_name": "Acme Corp",
    "brand_aliases": ["Acme", "ACME Inc"],
    "owned_domains": ["acme.com"],
    "unintended_domains": [],
    "competitors": [
        {"name": "Globex", "aliases": ["Globex Co"], "domains": ["globex.com"]}
    ],
    "country_code": "AU",
    "language_code": "en-AU",
    "benchmark_mode": "controlled_localized",
}


# --------------------------------------------------------------------------
# Normalization / dedupe hash
# --------------------------------------------------------------------------
class TestNormalization:
    def test_casefolds_and_collapses_whitespace(self) -> None:
        assert normalize_prompt_text("  Best   Running\tShoes ") == "best running shoes"

    def test_strips_trailing_punctuation(self) -> None:
        assert normalize_prompt_text("best shoes?") == "best shoes"
        assert normalize_prompt_text("best shoes!?  ") == "best shoes"

    def test_interior_punctuation_is_kept(self) -> None:
        assert normalize_prompt_text("acme vs. globex") == "acme vs. globex"

    def test_equivalent_texts_hash_identically(self) -> None:
        assert prompt_text_hash("Best Shoes?") == prompt_text_hash("best  shoes")

    def test_different_concepts_hash_differently(self) -> None:
        assert prompt_text_hash("best shoes") != prompt_text_hash("best hats")


# --------------------------------------------------------------------------
# Agent-output parsing
# --------------------------------------------------------------------------
class TestParseGenerationOutput:
    def test_valid_output_parses(self) -> None:
        raw = json.dumps(
            {
                "topics": [
                    {
                        "name": "Footwear",
                        "prompts": [
                            {"text": "best running shoes", "intent": "discovery"},
                            {"text": "acme vs globex shoes", "intent": "comparison"},
                        ],
                    }
                ]
            }
        )
        topics = parse_generation_output(raw)
        assert len(topics) == 1
        assert topics[0].name == "Footwear"
        assert [p.intent for p in topics[0].prompts] == ["discovery", "comparison"]

    def test_unknown_intent_blanks(self) -> None:
        raw = json.dumps(
            {"topics": [{"name": "T", "prompts": [{"text": "x", "intent": "warp"}]}]}
        )
        assert parse_generation_output(raw)[0].prompts[0].intent == ""

    def test_intent_is_casefolded(self) -> None:
        raw = json.dumps(
            {
                "topics": [
                    {"name": "T", "prompts": [{"text": "x", "intent": "Discovery"}]}
                ]
            }
        )
        assert parse_generation_output(raw)[0].prompts[0].intent == "discovery"

    def test_duplicates_within_response_collapse(self) -> None:
        raw = json.dumps(
            {
                "topics": [
                    {"name": "A", "prompts": [{"text": "Best Shoes?"}]},
                    {"name": "B", "prompts": [{"text": "best  shoes"}]},
                ]
            }
        )
        topics = parse_generation_output(raw)
        assert len(topics) == 1  # topic B became empty and was dropped
        assert topics[0].name == "A"

    def test_empty_prompts_and_topics_dropped(self) -> None:
        raw = json.dumps(
            {
                "topics": [
                    {"name": "  ", "prompts": [{"text": "kept prompt"}]},
                    {"name": "Kept", "prompts": [{"text": "  "}, {"text": "ok"}]},
                ]
            }
        )
        topics = parse_generation_output(raw)
        assert [t.name for t in topics] == ["Kept"]
        assert [p.text for p in topics[0].prompts] == ["ok"]

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(GenerationOutputError):
            parse_generation_output("not json {")

    def test_wrong_shape_raises(self) -> None:
        with pytest.raises(GenerationOutputError):
            parse_generation_output(json.dumps({"topics": [{"prompts": []}]}))

    def test_no_usable_prompts_raises(self) -> None:
        with pytest.raises(GenerationOutputError):
            parse_generation_output(json.dumps({"topics": []}))


# --------------------------------------------------------------------------
# Request building
# --------------------------------------------------------------------------
class TestBuildGenerationUserMessage:
    def test_includes_brand_evidence_and_count(self) -> None:
        message = build_generation_user_message(
            brand_context=BRAND_CONTEXT,
            existing_topics=["Footwear"],
            existing_prompts=["best running shoes"],
            count=5,
            intents=["discovery"],
        )
        assert "Acme Corp" in message
        assert "Globex" in message
        assert "Existing topics: Footwear" in message
        assert "exactly 5 prompts" in message
        assert "Restrict prompt intents to: discovery" in message
        assert "best running shoes" in message

    def test_target_topic_replaces_topic_list(self) -> None:
        message = build_generation_user_message(
            brand_context=BRAND_CONTEXT,
            existing_topics=["Footwear", "Apparel"],
            existing_prompts=[],
            count=3,
            intents=[],
            target_topic="Footwear",
        )
        assert "ONLY for this topic" in message
        assert "Footwear" in message
        assert "Existing topics:" not in message

    def test_empty_lists_render_none_markers(self) -> None:
        message = build_generation_user_message(
            brand_context={**BRAND_CONTEXT, "brand_aliases": [], "competitors": []},
            existing_topics=[],
            existing_prompts=[],
            count=3,
            intents=[],
        )
        assert "Brand aliases: none" in message
        assert "Competitors: none" in message
        assert "do NOT duplicate" not in message
