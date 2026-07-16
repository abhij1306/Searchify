# Deterministic rule evaluation (Task 5).
#
# Evaluates the config-owned ``SITE_HEALTH_RULES`` catalog against a page-facts
# dict (produced by ``parser.extract_page_facts``) into one ``RuleEvaluation``
# per rule. Each evaluation carries an outcome
# (pass / fail / not_applicable / error), a bounded exact ``evidence`` dict, and
# the rule's dimension/category/severity/weight/version for provenance.
#
# PURE + deterministic (no I/O, no ORM). Applicability is driven by the rule's
# ``applicability_key`` ("always" | "has_html"). If a rule's check raises, its
# outcome is ERROR (preserved, given zero scoring credit) — a single broken
# check never aborts the whole evaluation.
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config.site_health import (
    RULE_OUTCOME_ERROR,
    RULE_OUTCOME_FAIL,
    RULE_OUTCOME_NOT_APPLICABLE,
    RULE_OUTCOME_PASS,
    SITE_HEALTH_RULES,
    SITE_HEALTH_RULES_BY_ID,
    SiteHealthRule,
)

# Minimum extractable words for the AEO "sufficient text" rule. A page below
# this is answer-thin. Kept here (analysis-owned heuristic threshold) rather
# than in the rule catalog, which owns only rule metadata.
MIN_SUFFICIENT_WORDS = 100


@dataclass(frozen=True)
class RuleEvaluation:
    """The bounded, deterministic result of evaluating one rule.

    Immutable value type the worker persists as a ``SiteRuleEvaluation`` row.
    ``outcome`` is a config ``RULE_OUTCOME_*`` token; ``evidence`` is a small
    JSON-safe dict of exactly what drove the outcome.
    """

    rule_id: str
    rule_version: str
    dimension: str
    category: str
    severity: str
    weight: float
    outcome: str
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation: str = ""


def _pass_fail(condition: bool) -> str:
    return RULE_OUTCOME_PASS if condition else RULE_OUTCOME_FAIL


# --- individual checks: (facts) -> (outcome, evidence) --------------------


def _check_title_present(facts: dict) -> tuple[str, dict]:
    title = (facts.get("title") or "").strip()
    return _pass_fail(bool(title)), {
        "title_length": len(title),
        "present": bool(title),
    }


def _check_meta_description_present(facts: dict) -> tuple[str, dict]:
    desc = (facts.get("meta_description") or "").strip()
    return _pass_fail(bool(desc)), {
        "description_length": len(desc),
        "present": bool(desc),
    }


def _check_canonical_present(facts: dict) -> tuple[str, dict]:
    canonical = (facts.get("canonical_url") or "").strip()
    return _pass_fail(bool(canonical)), {
        "canonical_url": canonical,
        "present": bool(canonical),
    }


def _check_indexable(facts: dict) -> tuple[str, dict]:
    robots = facts.get("robots") or {}
    noindex = bool(robots.get("noindex"))
    # noindex -> fail (not indexable); otherwise pass.
    return _pass_fail(not noindex), {
        "noindex": noindex,
        "nofollow": bool(robots.get("nofollow")),
    }


def _check_https(facts: dict) -> tuple[str, dict]:
    delivery = facts.get("delivery") or {}
    is_https = bool(delivery.get("is_https"))
    return _pass_fail(is_https), {
        "scheme": delivery.get("scheme", ""),
        "final_url": delivery.get("final_url", ""),
        "is_https": is_https,
    }


def _check_single_h1(facts: dict) -> tuple[str, dict]:
    headings = facts.get("headings") or {}
    h1_count = int(headings.get("h1_count", 0) or 0)
    return _pass_fail(h1_count == 1), {"h1_count": h1_count}


def _check_structured_data_present(facts: dict) -> tuple[str, dict]:
    sd = facts.get("structured_data") or {}
    count = int(sd.get("count", 0) or 0)
    return _pass_fail(count > 0), {
        "block_count": count,
        "has_json_ld": bool(sd.get("has_json_ld")),
        "has_microdata": bool(sd.get("has_microdata")),
        "types": list(sd.get("types") or []),
    }


def _check_open_graph_present(facts: dict) -> tuple[str, dict]:
    og = facts.get("open_graph") or {}
    has_title = bool((og.get("og:title") or "").strip())
    has_desc = bool((og.get("og:description") or "").strip())
    present = has_title and has_desc
    return _pass_fail(present), {
        "has_og_title": has_title,
        "has_og_description": has_desc,
        "property_count": len(og),
    }


def _check_sufficient_text(facts: dict) -> tuple[str, dict]:
    body = facts.get("body") or {}
    word_count = int(body.get("word_count", 0) or 0)
    return _pass_fail(word_count >= MIN_SUFFICIENT_WORDS), {
        "word_count": word_count,
        "minimum": MIN_SUFFICIENT_WORDS,
    }


# Map each config rule_id to its concrete check. A rule in the catalog with no
# mapped check evaluates to ERROR (a wiring bug, preserved with zero credit).
_CHECKS: dict[str, Callable[[dict], tuple[str, dict]]] = {
    "technical.title_present": _check_title_present,
    "technical.meta_description_present": _check_meta_description_present,
    "technical.canonical_present": _check_canonical_present,
    "technical.indexable": _check_indexable,
    "technical.https": _check_https,
    "technical.single_h1": _check_single_h1,
    "aeo.structured_data_present": _check_structured_data_present,
    "aeo.open_graph_present": _check_open_graph_present,
    "aeo.sufficient_text": _check_sufficient_text,
}


def _is_applicable(rule: SiteHealthRule, facts: dict) -> bool:
    key = (rule.applicability_key or "always").strip().lower()
    if key == "always":
        return True
    if key == "has_html":
        return bool(facts.get("has_html"))
    # Unknown applicability key: treat as inapplicable (fail-closed).
    return False


def evaluate_rule(rule: SiteHealthRule, facts: dict) -> RuleEvaluation:
    """Evaluate one rule against ``facts`` into a ``RuleEvaluation``.

    Not-applicable rules short-circuit to NOT_APPLICABLE (excluded from
    scoring). A check that raises yields ERROR (preserved, zero credit). Never
    raises.
    """
    base = dict(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        dimension=rule.dimension,
        category=rule.category,
        severity=rule.severity,
        weight=float(rule.weight),
        remediation=rule.remediation,
    )
    if not _is_applicable(rule, facts):
        return RuleEvaluation(
            outcome=RULE_OUTCOME_NOT_APPLICABLE,
            evidence={"reason": "not_applicable"},
            **base,
        )
    check = _CHECKS.get(rule.rule_id)
    if check is None:
        return RuleEvaluation(
            outcome=RULE_OUTCOME_ERROR,
            evidence={"error": "no_check_mapped"},
            **base,
        )
    try:
        outcome, evidence = check(facts)
    except Exception as exc:  # a broken check is ERROR, never a crash
        return RuleEvaluation(
            outcome=RULE_OUTCOME_ERROR,
            evidence={"error": f"{type(exc).__name__}: {exc}"[:512]},
            **base,
        )
    return RuleEvaluation(outcome=outcome, evidence=evidence, **base)


def evaluate_all(facts: dict) -> list[RuleEvaluation]:
    """Evaluate every catalog rule against ``facts`` (catalog order)."""
    return [evaluate_rule(rule, facts) for rule in SITE_HEALTH_RULES]


def rule_for(rule_id: str) -> SiteHealthRule | None:
    """Convenience lookup of a catalog rule by id (or None)."""
    return SITE_HEALTH_RULES_BY_ID.get(rule_id)
