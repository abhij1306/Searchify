"""Unit tests for the Site Health rule evaluator (Task 5).

Verifies each rule maps to the right check and each outcome (pass / fail /
not_applicable / error) is produced with exact evidence + provenance. Pure,
offline.
"""

from __future__ import annotations

from app.analysis.site_health.rules import (
    MIN_SUFFICIENT_WORDS,
    evaluate_all,
    evaluate_rule,
    rule_for,
)
from app.core.config.site_health import (
    DIMENSION_AEO,
    DIMENSION_TECHNICAL,
    RULE_OUTCOME_ERROR,
    RULE_OUTCOME_FAIL,
    RULE_OUTCOME_NOT_APPLICABLE,
    RULE_OUTCOME_PASS,
    SITE_HEALTH_RULES,
    SiteHealthRule,
)


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
