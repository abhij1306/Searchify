"""Unit tests for the v2 P1 page-type classifier (spec §5.1).

Covers every signal in the fixed priority order (root path, URL path
patterns, content heuristics, structured-data types), the deliberate
conflict semantics (URL/content signals 1-3 outrank the schema signal 4),
the confidence threshold fallback to ``other``, homepage path equivalents,
bounded evidence contents, and determinism. Pure, offline.
"""

from __future__ import annotations

import pytest

from app.analysis.site_health.page_types import classify
from app.core.config import site_health as config
from app.core.config.site_health import (
    CLASSIFIER_VERSION,
    PAGE_TYPE_CONFIDENCE_THRESHOLD,
    PAGE_TYPE_PATH_PATTERNS,
    PAGE_TYPE_PROFILES,
    PAGE_TYPE_SCHEMA_TYPE_MAP,
    PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC,
    PAGE_TYPE_SIGNAL_NONE,
    PAGE_TYPE_SIGNAL_PATH_PATTERN,
    PAGE_TYPE_SIGNAL_ROOT_PATH,
    PAGE_TYPE_SIGNAL_STRUCTURED_DATA,
    PAGE_TYPE_SIGNAL_WEIGHTS,
    PAGE_TYPES,
)


def _facts(
    *,
    h2_texts: list[str] | None = None,
    body_text: str = "",
    schema_types: list[str] | None = None,
) -> dict:
    """A bounded parser-facts-shaped dict with only what classify() reads."""
    return {
        "headings": {"h2_texts": h2_texts or []},
        "body": {"text": body_text, "word_count": len(body_text.split())},
        "structured_data": {"types": schema_types or []},
    }


def _question_h2s(count: int, *, total: int | None = None) -> list[str]:
    """``total`` h2 texts of which ``count`` are question-form."""
    total = total if total is not None else count
    return [f"What is topic {i}?" for i in range(count)] + [
        f"Statement heading {i}" for i in range(total - count)
    ]


_PRODUCT_TEXT = "This durable water bottle costs $19.99. Add to cart today."
_ARTICLE_TEXT = "By Jane Doe\nMarch 3, 2026\nAn in-depth look at the topic."


# --- Signal 1: root path -> homepage ---------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://example.com",
        "https://example.com/index.html",
        "https://example.com/INDEX.HTML",
        "https://example.com/en/",
        "https://example.com/en",
        "https://example.com/en-us/",
        "https://example.com/?utm_source=x",
    ],
)
def test_root_path_equivalents_classify_homepage(url: str) -> None:
    assessment = classify(url, _facts())
    assert assessment.page_type == "homepage"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_ROOT_PATH
    assert assessment.confidence >= PAGE_TYPE_CONFIDENCE_THRESHOLD


def test_unlisted_locale_root_falls_through_to_other() -> None:
    # "/uk/" is deliberately NOT in HOMEPAGE_PATH_EQUIVALENTS.
    assessment = classify("https://example.com/uk/", _facts())
    assert assessment.page_type == "other"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_NONE
    assert assessment.confidence == 0.0


def test_homepage_outranks_conflicting_schema_and_records_suggestion() -> None:
    assessment = classify("https://example.com/", _facts(schema_types=["FAQPage"]))
    assert assessment.page_type == "homepage"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_ROOT_PATH
    # The schema claim is recorded even though it lost.
    assert assessment.schema_suggested_type == "faq"


# --- Signal 2: ordered URL path patterns ------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/blog/my-post", "article"),
        ("https://example.com/news/story", "article"),
        ("https://example.com/guides/how-to", "article"),
        ("https://example.com/product/123", "product"),
        ("https://example.com/products/123", "product"),
        ("https://example.com/p/abc", "product"),
        ("https://example.com/shop/item", "product"),
        ("https://example.com/category/shoes", "category"),
        ("https://example.com/collections/summer", "category"),
        ("https://example.com/pricing", "pricing"),
        ("https://example.com/pricing/teams", "pricing"),
        ("https://example.com/docs/getting-started", "docs"),
        ("https://example.com/reference/api", "docs"),
        ("https://example.com/faq", "faq"),
        ("https://example.com/help/article", "faq"),
        ("https://example.com/about", "about_contact"),
        ("https://example.com/contact", "about_contact"),
    ],
)
def test_path_patterns_classify_each_type(url: str, expected: str) -> None:
    assessment = classify(url, _facts())
    assert assessment.page_type == expected
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_PATH_PATTERN


def test_path_pattern_first_match_wins_in_config_order() -> None:
    # /blog/pricing matches both the article (blog) and pricing patterns;
    # the article entry is earlier in the ordered config table.
    assessment = classify("https://example.com/blog/pricing", _facts())
    assert assessment.page_type == "article"


def test_path_pattern_does_not_match_unanchored_segments() -> None:
    # Patterns are anchored at the path root: "/x/blog" is not /blog/.
    assessment = classify("https://example.com/x/blog", _facts())
    assert assessment.page_type == "other"


def test_path_pattern_outranks_schema_on_conflict() -> None:
    assessment = classify(
        "https://example.com/product/123",
        _facts(schema_types=["Article"]),
    )
    assert assessment.page_type == "product"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_PATH_PATTERN
    assert assessment.schema_suggested_type == "article"


# --- Signal 3: content/heading heuristics -----------------------------------


def test_question_heading_ratio_classifies_faq() -> None:
    facts = _facts(h2_texts=_question_h2s(4, total=5))
    assessment = classify("https://example.com/support", facts)
    assert assessment.page_type == "faq"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC


def test_faq_requires_minimum_heading_count() -> None:
    # 2/2 question headings is a perfect ratio but below the minimum count.
    facts = _facts(h2_texts=_question_h2s(2, total=2))
    assert classify("https://example.com/support", facts).page_type == "other"


def test_faq_requires_question_ratio() -> None:
    # 1 question of 4 headings is below the config ratio.
    facts = _facts(h2_texts=_question_h2s(1, total=4))
    assert classify("https://example.com/support", facts).page_type == "other"


def test_question_word_prefix_counts_as_question_form() -> None:
    facts = _facts(h2_texts=["How it works", "Why choose us", "What you get"])
    assert classify("https://example.com/support", facts).page_type == "faq"


def test_price_and_cart_markers_classify_product() -> None:
    assessment = classify("https://example.com/item", _facts(body_text=_PRODUCT_TEXT))
    assert assessment.page_type == "product"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC


def test_price_without_cart_marker_does_not_classify_product() -> None:
    facts = _facts(body_text="Everything here costs $19.99, shipping included.")
    assert classify("https://example.com/item", facts).page_type == "other"


def test_cart_marker_without_price_does_not_classify_product() -> None:
    facts = _facts(body_text="Click add to cart whenever you are ready.")
    assert classify("https://example.com/item", facts).page_type == "other"


def test_byline_and_date_classify_article() -> None:
    assessment = classify("https://example.com/post", _facts(body_text=_ARTICLE_TEXT))
    assert assessment.page_type == "article"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC


def test_byline_without_date_does_not_classify_article() -> None:
    facts = _facts(body_text="By Jane Doe\nAn undated musing on the topic.")
    assert classify("https://example.com/post", facts).page_type == "other"


def test_content_heuristics_have_fixed_sub_order() -> None:
    # FAQ outranks product within signal 3 when both match.
    facts = _facts(h2_texts=_question_h2s(3), body_text=_PRODUCT_TEXT)
    assert classify("https://example.com/x", facts).page_type == "faq"


def test_content_heuristic_outranks_schema_on_conflict() -> None:
    facts = _facts(body_text=_PRODUCT_TEXT, schema_types=["Article"])
    assessment = classify("https://example.com/item", facts)
    assert assessment.page_type == "product"
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC
    assert assessment.schema_suggested_type == "article"


# --- Signal 4: structured-data types -----------------------------------------


@pytest.mark.parametrize(
    ("schema_type", "expected"),
    [
        ("Article", "article"),
        ("BlogPosting", "article"),
        ("NewsArticle", "article"),
        ("Product", "product"),
        ("FAQPage", "faq"),
        ("TechArticle", "docs"),
    ],
)
def test_schema_types_map_to_page_types(schema_type: str, expected: str) -> None:
    assessment = classify(
        "https://example.com/anything", _facts(schema_types=[schema_type])
    )
    assert assessment.page_type == expected
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_STRUCTURED_DATA
    assert assessment.schema_suggested_type == expected


def test_unmapped_schema_type_does_not_classify() -> None:
    assessment = classify(
        "https://example.com/anything", _facts(schema_types=["Organization"])
    )
    assert assessment.page_type == "other"
    assert assessment.schema_suggested_type is None


def test_multiple_schema_types_first_mapped_in_sorted_order_wins() -> None:
    assessment = classify(
        "https://example.com/anything",
        _facts(schema_types=["Product", "Article"]),
    )
    # Sorted type names put Article first.
    assert assessment.page_type == "article"
    assert assessment.schema_suggested_type == "article"


# --- Confidence, threshold, evidence, determinism ----------------------------


def test_confidence_is_sum_of_matched_signal_weights() -> None:
    facts = _facts(schema_types=["BlogPosting"])
    assessment = classify("https://example.com/blog/x", facts)
    expected = round(
        PAGE_TYPE_SIGNAL_WEIGHTS[PAGE_TYPE_SIGNAL_PATH_PATTERN]
        + PAGE_TYPE_SIGNAL_WEIGHTS[PAGE_TYPE_SIGNAL_STRUCTURED_DATA],
        4,
    )
    assert assessment.confidence == pytest.approx(expected)


def test_below_threshold_falls_back_to_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A raised threshold makes even a matched (schema-only) page fall back.
    monkeypatch.setattr(
        config,
        "PAGE_TYPE_CONFIDENCE_THRESHOLD",
        PAGE_TYPE_SIGNAL_WEIGHTS[PAGE_TYPE_SIGNAL_STRUCTURED_DATA] + 0.1,
    )
    assessment = classify(
        "https://example.com/anything", _facts(schema_types=["Article"])
    )
    assert assessment.page_type == "other"
    # The matched signal is still recorded for explainability.
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_STRUCTURED_DATA
    assert assessment.confidence < config.PAGE_TYPE_CONFIDENCE_THRESHOLD


def test_no_signals_classifies_other_with_none_classifier() -> None:
    assessment = classify("https://example.com/some/random-page", _facts())
    assert assessment.page_type == "other"
    assert assessment.confidence == 0.0
    assert assessment.classified_by == PAGE_TYPE_SIGNAL_NONE
    assert assessment.signals == ()


def test_evidence_is_bounded_and_explainable() -> None:
    facts = _facts(schema_types=["Article"])
    assessment = classify("https://example.com/product/123", facts)
    evidence = assessment.to_evidence()
    assert evidence["classifier_version"] == CLASSIFIER_VERSION
    assert evidence["classified_by"] == PAGE_TYPE_SIGNAL_PATH_PATTERN
    assert evidence["schema_suggested_type"] == "article"
    assert evidence["confidence"] == assessment.confidence
    assert evidence["confidence_threshold"] == PAGE_TYPE_CONFIDENCE_THRESHOLD
    # At most one signal record per signal source, each small + JSON-safe.
    assert len(evidence["signals"]) <= 4
    for signal in evidence["signals"]:
        assert set(signal) == {"signal", "page_type", "weight", "detail"}
        assert signal["page_type"] in PAGE_TYPES
        assert len(signal["detail"]) <= 256


def test_classification_is_deterministic() -> None:
    facts = _facts(
        h2_texts=_question_h2s(3),
        body_text=_PRODUCT_TEXT + " " + _ARTICLE_TEXT,
        schema_types=["Product"],
    )
    first = classify("https://example.com/page", facts)
    second = classify("https://example.com/page", facts)
    assert first == second
    assert first.to_evidence() == second.to_evidence()


def test_malformed_inputs_never_raise() -> None:
    # An empty/unparseable URL normalizes to the root path (deterministic).
    assert classify("", {}).page_type == "homepage"
    assert classify("not a url at all", {}).page_type == "other"
    # Missing/partial facts dicts simply match fewer signals.
    assert classify("https://example.com/blog/x", {}).page_type == "article"
    assessment = classify("https://example.com/blog/x", None)  # type: ignore[arg-type]
    assert assessment.page_type == "article"


def test_classifier_version_stamped_from_config() -> None:
    assessment = classify("https://example.com/", {})
    assert assessment.classifier_version == CLASSIFIER_VERSION


# --- Config table integrity (static frozen tables — a plain test, not an
# import-time check) --------------------------------------------------------


def test_page_type_config_tables_are_internally_consistent() -> None:
    # Every taxonomy member has a profile with a sane thin-content minimum.
    for page_type in PAGE_TYPES:
        profile = PAGE_TYPE_PROFILES.get(page_type)
        assert profile is not None, f"missing PAGE_TYPE_PROFILES entry: {page_type}"
        assert profile.min_sufficient_words >= 0
    # Path patterns and the schema map only reference taxonomy members.
    for page_type, _pattern in PAGE_TYPE_PATH_PATTERNS:
        assert page_type in PAGE_TYPES, f"path pattern type unknown: {page_type}"
    for page_type in PAGE_TYPE_SCHEMA_TYPE_MAP.values():
        assert page_type in PAGE_TYPES, f"schema map type unknown: {page_type}"
    # Every signal name the classifier records has a weight.
    for signal in (
        config.PAGE_TYPE_SIGNAL_ROOT_PATH,
        config.PAGE_TYPE_SIGNAL_PATH_PATTERN,
        config.PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC,
        config.PAGE_TYPE_SIGNAL_STRUCTURED_DATA,
    ):
        assert signal in PAGE_TYPE_SIGNAL_WEIGHTS
