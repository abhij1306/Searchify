"""Unit tests for the Site Health rule evaluator (Task 5 + v2 P1).

Verifies each rule maps to the right check and each outcome (pass / fail /
not_applicable / error) is produced with exact evidence + provenance, plus
the v2 P1 page-type behavior: ``page_type:<type>`` applicability tokens,
per-type thin-content minimums (the v1 ``MIN_SUFFICIENT_WORDS`` analysis
constant moved into the config-owned ``PAGE_TYPE_PROFILES``), and
per-(rule_id, page_type) weight overrides. Pure, offline.
"""

from __future__ import annotations

from app.analysis.site_health.rules import (
    evaluate_all,
    evaluate_rule,
    rule_for,
)
from app.core.config.site_health import (
    DIMENSION_AEO,
    DIMENSION_TECHNICAL,
    PAGE_TYPE_OTHER,
    PAGE_TYPE_PROFILES,
    RULE_OUTCOME_ERROR,
    RULE_OUTCOME_FAIL,
    RULE_OUTCOME_NOT_APPLICABLE,
    RULE_OUTCOME_PASS,
    SITE_HEALTH_RULES,
    SiteHealthRule,
)

# The v1 global thin-content minimum now lives in the config-owned ``other``
# profile (identical value, so unclassified pages score exactly as before).
MIN_SUFFICIENT_WORDS = PAGE_TYPE_PROFILES[PAGE_TYPE_OTHER].min_sufficient_words


def test_other_profile_minimum_preserves_v1_parity():
    # Pin the v1 contract: the ``other`` profile minimum must stay 100 words
    # so unclassified pages score exactly as v1 did (spec §5.2). The alias
    # above intentionally derives from config; this assertion does not.
    assert PAGE_TYPE_PROFILES[PAGE_TYPE_OTHER].min_sufficient_words == 100


def _html_facts(**overrides):
    facts = {
        "has_html": True,
        "title": "A title",
        "meta_description": "A description",
        "canonical_url": "https://x.example/",
        "robots": {"noindex": False, "nofollow": False},
        "delivery": {
            "is_https": True,
            "scheme": "https",
            "final_url": "https://x.example/",
        },
        "headings": {"h1_count": 1},
        "structured_data": {
            "count": 1,
            "has_json_ld": True,
            "has_microdata": False,
            "types": ["Organization"],
        },
        "open_graph": {"og:title": "T", "og:description": "D"},
        "body": {"word_count": MIN_SUFFICIENT_WORDS + 10},
    }
    facts.update(overrides)
    return facts


def _outcome(facts, rule_id):
    rule = rule_for(rule_id)
    assert rule is not None
    return evaluate_rule(rule, facts)


def test_all_rules_pass_on_healthy_page():
    facts = _html_facts()
    evals = evaluate_all(facts)
    assert {e.rule_id for e in evals} == {r.rule_id for r in SITE_HEALTH_RULES}
    assert all(e.outcome == RULE_OUTCOME_PASS for e in evals)
    # Provenance carried through from the catalog.
    title_eval = next(e for e in evals if e.rule_id == "technical.title_present")
    assert title_eval.dimension == DIMENSION_TECHNICAL
    assert title_eval.weight == 3.0
    assert title_eval.remediation


def test_title_absent_fails():
    ev = _outcome(_html_facts(title=""), "technical.title_present")
    assert ev.outcome == RULE_OUTCOME_FAIL
    assert ev.evidence["present"] is False


def test_meta_description_absent_fails():
    ev = _outcome(
        _html_facts(meta_description=""),
        "technical.meta_description_present",
    )
    assert ev.outcome == RULE_OUTCOME_FAIL


def test_canonical_absent_fails():
    ev = _outcome(_html_facts(canonical_url=""), "technical.canonical_present")
    assert ev.outcome == RULE_OUTCOME_FAIL


def test_noindex_fails_indexable():
    facts = _html_facts(robots={"noindex": True, "nofollow": False})
    ev = _outcome(facts, "technical.indexable")
    assert ev.outcome == RULE_OUTCOME_FAIL
    assert ev.evidence["noindex"] is True


def test_http_fails_https_rule():
    facts = _html_facts(
        delivery={"is_https": False, "scheme": "http", "final_url": "http://x"}
    )
    ev = _outcome(facts, "technical.https")
    assert ev.outcome == RULE_OUTCOME_FAIL


def test_zero_or_multiple_h1_fails_single_h1():
    assert (
        _outcome(_html_facts(headings={"h1_count": 0}), "technical.single_h1").outcome
        == RULE_OUTCOME_FAIL
    )
    assert (
        _outcome(_html_facts(headings={"h1_count": 2}), "technical.single_h1").outcome
        == RULE_OUTCOME_FAIL
    )


def test_structured_data_absent_fails():
    facts = _html_facts(
        structured_data={
            "count": 0,
            "has_json_ld": False,
            "has_microdata": False,
            "types": [],
        }
    )
    ev = _outcome(facts, "aeo.structured_data_present")
    assert ev.outcome == RULE_OUTCOME_FAIL


def test_open_graph_incomplete_fails():
    ev = _outcome(_html_facts(open_graph={"og:title": "T"}), "aeo.open_graph_present")
    assert ev.outcome == RULE_OUTCOME_FAIL
    assert ev.evidence["has_og_description"] is False


def test_insufficient_text_fails():
    ev = _outcome(
        _html_facts(body={"word_count": MIN_SUFFICIENT_WORDS - 1}),
        "aeo.sufficient_text",
    )
    assert ev.outcome == RULE_OUTCOME_FAIL
    assert ev.evidence["minimum"] == MIN_SUFFICIENT_WORDS


def test_has_html_rules_not_applicable_without_html():
    # A non-HTML page (has_html False) makes the has_html rules N/A but leaves
    # the "always" rules applicable.
    facts = {
        "has_html": False,
        "title": "",
        "meta_description": "",
        "canonical_url": "",
        "robots": {"noindex": False, "nofollow": False},
        "delivery": {"is_https": True, "scheme": "https", "final_url": "x"},
    }
    evals = {e.rule_id: e for e in evaluate_all(facts)}
    assert evals["technical.single_h1"].outcome == RULE_OUTCOME_NOT_APPLICABLE
    assert evals["aeo.structured_data_present"].outcome == RULE_OUTCOME_NOT_APPLICABLE
    assert evals["aeo.open_graph_present"].outcome == RULE_OUTCOME_NOT_APPLICABLE
    assert evals["aeo.sufficient_text"].outcome == RULE_OUTCOME_NOT_APPLICABLE
    # "always" rules still evaluate (https passes; title fails).
    assert evals["technical.https"].outcome == RULE_OUTCOME_PASS
    assert evals["technical.title_present"].outcome == RULE_OUTCOME_FAIL


def test_check_raising_yields_error_outcome():
    # A rule whose facts are shaped so its check raises must yield ERROR, never
    # propagate. Feed a facts dict where headings is not a dict for single_h1.
    bad = _html_facts(headings=None)
    # headings None -> `.get` on None raises inside the check.
    rule = rule_for("technical.single_h1")
    facts = dict(bad)
    facts["headings"] = 12345  # int has no .get -> AttributeError in check
    ev = evaluate_rule(rule, facts)
    assert ev.outcome == RULE_OUTCOME_ERROR
    assert "error" in ev.evidence


def test_unmapped_rule_id_yields_error():
    phantom = SiteHealthRule(
        rule_id="aeo.does_not_exist",
        rule_version="v1",
        dimension=DIMENSION_AEO,
        category="content",
        severity="low",
        weight=1.0,
        applicability_key="always",
        description="",
        remediation="",
    )
    ev = evaluate_rule(phantom, _html_facts())
    assert ev.outcome == RULE_OUTCOME_ERROR
    assert ev.evidence["error"] == "no_check_mapped"


def test_unknown_applicability_key_is_not_applicable():
    phantom = SiteHealthRule(
        rule_id="technical.title_present",
        rule_version="v1",
        dimension=DIMENSION_TECHNICAL,
        category="metadata",
        severity="low",
        weight=1.0,
        applicability_key="some_unknown_key",
        description="",
        remediation="",
    )
    ev = evaluate_rule(phantom, _html_facts())
    assert ev.outcome == RULE_OUTCOME_NOT_APPLICABLE


# --- v2 P1: page-type applicability / minimums / weight overrides ---------


def _page_type_rule(rule_id: str, page_type: str, weight: float = 1.0):
    """A catalog-shaped rule scoped to one page type via the token syntax."""
    return SiteHealthRule(
        rule_id=rule_id,
        rule_version="v1",
        dimension=DIMENSION_TECHNICAL,
        category="content",
        severity="low",
        weight=weight,
        applicability_key=f"page_type:{page_type}",
        description="",
        remediation="",
    )


def test_page_type_token_applicable_on_matching_type():
    rule = _page_type_rule("technical.title_present", "article")
    ev = evaluate_rule(rule, _html_facts(page_type="article"))
    # Applicable -> the real check runs (title present -> pass).
    assert ev.outcome == RULE_OUTCOME_PASS


def test_page_type_token_not_applicable_on_other_type():
    rule = _page_type_rule("technical.title_present", "article")
    ev = evaluate_rule(rule, _html_facts(page_type="product"))
    assert ev.outcome == RULE_OUTCOME_NOT_APPLICABLE


def test_page_type_token_not_applicable_without_page_type_fact():
    # No facts["page_type"] (e.g. pre-classification) -> fail-closed.
    rule = _page_type_rule("technical.title_present", "article")
    ev = evaluate_rule(rule, _html_facts())
    assert ev.outcome == RULE_OUTCOME_NOT_APPLICABLE


def test_page_type_token_unknown_type_in_facts_fail_closed():
    # A page_type outside the config taxonomy has no profile -> fail-closed.
    rule = _page_type_rule("technical.title_present", "article")
    ev = evaluate_rule(rule, _html_facts(page_type="not_a_real_type"))
    assert ev.outcome == RULE_OUTCOME_NOT_APPLICABLE


def test_page_type_token_for_unconfigured_type_fail_closed():
    # The token itself names a type with no profile entry -> fail-closed.
    rule = _page_type_rule("technical.title_present", "not_a_real_type")
    ev = evaluate_rule(rule, _html_facts(page_type="article"))
    assert ev.outcome == RULE_OUTCOME_NOT_APPLICABLE


def test_sufficient_text_uses_per_type_minimum():
    article_min = PAGE_TYPE_PROFILES["article"].min_sufficient_words
    other_min = PAGE_TYPE_PROFILES[PAGE_TYPE_OTHER].min_sufficient_words
    assert article_min > other_min  # the config actually differentiates
    # Between the two minimums: an article fails while `other` passes.
    facts_article = _html_facts(
        page_type="article", body={"word_count": other_min}
    )
    ev = _outcome(facts_article, "aeo.sufficient_text")
    assert ev.outcome == RULE_OUTCOME_FAIL
    assert ev.evidence["minimum"] == article_min
    assert ev.evidence["page_type"] == "article"
    facts_other = _html_facts(page_type="other", body={"word_count": other_min})
    ev_other = _outcome(facts_other, "aeo.sufficient_text")
    assert ev_other.outcome == RULE_OUTCOME_PASS
    assert ev_other.evidence["minimum"] == other_min


def test_sufficient_text_without_page_type_falls_back_to_other_minimum():
    ev = _outcome(
        _html_facts(body={"word_count": MIN_SUFFICIENT_WORDS}),
        "aeo.sufficient_text",
    )
    assert ev.outcome == RULE_OUTCOME_PASS
    assert ev.evidence["minimum"] == MIN_SUFFICIENT_WORDS
    assert ev.evidence["page_type"] == "other"


def test_sufficient_text_homepage_minimum_is_lower():
    homepage_min = PAGE_TYPE_PROFILES["homepage"].min_sufficient_words
    assert homepage_min < MIN_SUFFICIENT_WORDS
    ev = _outcome(
        _html_facts(page_type="homepage", body={"word_count": homepage_min}),
        "aeo.sufficient_text",
    )
    assert ev.outcome == RULE_OUTCOME_PASS
    assert ev.evidence["minimum"] == homepage_min


def test_weight_override_applies_for_configured_page_type():
    override = PAGE_TYPE_PROFILES["homepage"].rule_weight_overrides[
        "aeo.sufficient_text"
    ]
    base_weight = rule_for("aeo.sufficient_text").weight
    assert override != base_weight  # the sparse config override is real
    ev = _outcome(_html_facts(page_type="homepage"), "aeo.sufficient_text")
    assert ev.weight == override
    # Every other page type keeps the catalog weight.
    ev_other = _outcome(_html_facts(page_type="other"), "aeo.sufficient_text")
    assert ev_other.weight == base_weight
    # And a page with no page_type fact keeps the catalog weight.
    ev_plain = _outcome(_html_facts(), "aeo.sufficient_text")
    assert ev_plain.weight == base_weight
