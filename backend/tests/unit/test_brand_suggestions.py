"""Unit tests for the brand-suggestion builders/parsers (setup-form AI).

Deterministic fixtures only — no live provider calls (mirrors
``test_prompt_generation.py``: unit-test the parser/dedupe against fixture
model output).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config.suggestions import BrandSuggestionSettings
from app.domain.projects.suggestions import (
    SuggestionOutputError,
    SuggestionValidationError,
    _normalize_domain,
    build_competitor_user_message,
    build_owned_domain_user_message,
    parse_competitor_output,
    parse_owned_domain_output,
    validate_suggestion_payload,
)

BRAND_CONTEXT = {
    "brand_name": "Acme Corp",
    "brand_aliases": ["Acme", "ACME Inc"],
    "website_url": "https://acme.com",
    "country_code": "AU",
    "language_code": "en-AU",
}


# --------------------------------------------------------------------------
# Domain normalization
# --------------------------------------------------------------------------
class TestNormalizeDomain:
    def test_bare_domain_passes_lowercased(self) -> None:
        assert _normalize_domain("Acme.COM") == "acme.com"

    def test_url_is_coerced_to_bare_domain(self) -> None:
        assert _normalize_domain("https://www.acme.com/products?x=1") == "acme.com"

    def test_port_and_userinfo_are_stripped(self) -> None:
        assert _normalize_domain("user@acme.com:8080") == "acme.com"

    def test_multi_label_tld_is_kept(self) -> None:
        assert _normalize_domain("shop.acme.co.uk") == "shop.acme.co.uk"

    def test_garbage_is_rejected(self) -> None:
        assert _normalize_domain("not a domain") is None
        assert _normalize_domain("") is None
        assert _normalize_domain("acme") is None
        assert _normalize_domain("-bad.com") is None


# --------------------------------------------------------------------------
# Agent-output parsing — competitors
# --------------------------------------------------------------------------
class TestParseCompetitorOutput:
    def test_valid_output_parses(self) -> None:
        raw = json.dumps(
            {
                "competitors": [
                    {
                        "name": "Globex",
                        "aliases": ["Globex Co"],
                        "domains": ["globex.com"],
                    },
                    {"name": "Initech", "aliases": [], "domains": []},
                ]
            }
        )
        competitors, dropped = parse_competitor_output(raw, existing_names=[])
        assert [c.name for c in competitors] == ["Globex", "Initech"]
        assert competitors[0].domains == ["globex.com"]
        assert dropped == 0

    def test_blank_names_are_dropped(self) -> None:
        raw = json.dumps(
            {"competitors": [{"name": "  "}, {"name": "Globex"}]}
        )
        competitors, _ = parse_competitor_output(raw, existing_names=[])
        assert [c.name for c in competitors] == ["Globex"]

    def test_dedupes_case_insensitively_within_response(self) -> None:
        raw = json.dumps(
            {"competitors": [{"name": "Globex"}, {"name": "GLOBEX"}]}
        )
        competitors, dropped = parse_competitor_output(raw, existing_names=[])
        assert [c.name for c in competitors] == ["Globex"]
        assert dropped == 1

    def test_dedupes_against_existing_names(self) -> None:
        raw = json.dumps(
            {"competitors": [{"name": "globex"}, {"name": "Initech"}]}
        )
        competitors, dropped = parse_competitor_output(
            raw, existing_names=["Globex"]
        )
        assert [c.name for c in competitors] == ["Initech"]
        assert dropped == 1

    def test_invalid_domains_are_normalized_or_dropped(self) -> None:
        raw = json.dumps(
            {
                "competitors": [
                    {
                        "name": "Globex",
                        "domains": ["https://globex.com/", "not a domain"],
                    }
                ]
            }
        )
        competitors, _ = parse_competitor_output(raw, existing_names=[])
        assert competitors[0].domains == ["globex.com"]

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(SuggestionOutputError):
            parse_competitor_output("this is not json", existing_names=[])

    def test_wrong_shape_raises(self) -> None:
        with pytest.raises(SuggestionOutputError):
            parse_competitor_output(
                json.dumps({"competitors": "nope"}), existing_names=[]
            )

    def test_no_usable_competitors_raises(self) -> None:
        with pytest.raises(SuggestionOutputError):
            parse_competitor_output(
                json.dumps({"competitors": []}), existing_names=[]
            )


# --------------------------------------------------------------------------
# Agent-output parsing — owned domains
# --------------------------------------------------------------------------
class TestParseOwnedDomainOutput:
    def test_valid_output_parses_and_normalizes(self) -> None:
        raw = json.dumps({"domains": ["Acme.com", "https://www.acme.co.uk/about"]})
        domains, dropped = parse_owned_domain_output(raw, existing_domains=[])
        assert domains == ["acme.com", "acme.co.uk"]
        assert dropped == 0

    def test_garbage_is_dropped_silently(self) -> None:
        raw = json.dumps({"domains": ["acme.com", "not a domain"]})
        domains, dropped = parse_owned_domain_output(raw, existing_domains=[])
        assert domains == ["acme.com"]
        assert dropped == 0

    def test_dedupes_within_response_and_against_existing(self) -> None:
        raw = json.dumps({"domains": ["acme.com", "ACME.com", "acme.io"]})
        domains, dropped = parse_owned_domain_output(
            raw, existing_domains=["acme.io"]
        )
        assert domains == ["acme.com"]
        assert dropped == 2

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(SuggestionOutputError):
            parse_owned_domain_output("[broken", existing_domains=[])

    def test_no_usable_domains_raises(self) -> None:
        with pytest.raises(SuggestionOutputError):
            parse_owned_domain_output(
                json.dumps({"domains": ["not a domain"]}), existing_domains=[]
            )


# --------------------------------------------------------------------------
# User-message builders
# --------------------------------------------------------------------------
class TestBuildCompetitorUserMessage:
    def test_includes_brand_evidence_and_count(self) -> None:
        message = build_competitor_user_message(
            brand_context=BRAND_CONTEXT, existing_names=[], count=5
        )
        assert "Brand: Acme Corp" in message
        assert "Acme, ACME Inc" in message
        assert "Website: https://acme.com" in message
        assert "Market country: AU" in message
        assert "Suggest exactly 5 competitors." in message
        assert "do NOT duplicate" not in message

    def test_existing_names_form_do_not_duplicate_block(self) -> None:
        message = build_competitor_user_message(
            brand_context=BRAND_CONTEXT, existing_names=["Globex"], count=3
        )
        assert "do NOT duplicate" in message
        assert "- Globex" in message


class TestBuildOwnedDomainUserMessage:
    def test_includes_brand_evidence_and_exclusions(self) -> None:
        message = build_owned_domain_user_message(
            brand_context=BRAND_CONTEXT, existing_domains=[], count=5
        )
        assert "Brand: Acme Corp" in message
        assert "NOT competitor domains" in message
        assert "unintended domains" in message

    def test_existing_domains_form_do_not_duplicate_block(self) -> None:
        message = build_owned_domain_user_message(
            brand_context=BRAND_CONTEXT, existing_domains=["acme.com"], count=5
        )
        assert "do NOT duplicate" in message
        assert "- acme.com" in message

    def test_empty_context_uses_markers(self) -> None:
        message = build_owned_domain_user_message(
            brand_context={"brand_name": "Acme"}, existing_domains=[], count=5
        )
        assert "Brand aliases: none" in message
        assert "Website: unspecified" in message
        assert "Market country: unspecified" in message


# --------------------------------------------------------------------------
# Payload validation (consent gate + bounds)
# --------------------------------------------------------------------------
def _payload(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {"confirm_send_evidence": True, "count": 5}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestValidateSuggestionPayload:
    def test_accepts_confirmed_in_bounds_payload(self) -> None:
        validate_suggestion_payload(_payload())

    def test_rejects_missing_consent(self) -> None:
        with pytest.raises(SuggestionValidationError, match="confirm_send_evidence"):
            validate_suggestion_payload(_payload(confirm_send_evidence=False))

    def test_rejects_count_over_cap(self) -> None:
        with pytest.raises(SuggestionValidationError, match="at most"):
            validate_suggestion_payload(_payload(count=10_000))


# --------------------------------------------------------------------------
# Settings bounds
# --------------------------------------------------------------------------
class TestBrandSuggestionSettings:
    def test_rejects_zero_default_count(self) -> None:
        with pytest.raises(ValidationError):
            BrandSuggestionSettings(BRAND_SUGGESTION_DEFAULT_COUNT=0)

    def test_rejects_negative_max_count(self) -> None:
        with pytest.raises(ValidationError):
            BrandSuggestionSettings(BRAND_SUGGESTION_MAX_COUNT=-1)
