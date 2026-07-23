"""Deterministic AI-referral classification (no LLM — invariant 9).

A PURE function over referral signals: no DB, no network, no clock. The rule
tables are config-owned data (``app/core/config/analytics.py``) versioned by
``AI_REFERRAL_RULE_VERSION`` so every classification traces to the exact
rules that produced it (invariant 4).

Rules are evaluated in a FIXED priority order — referrer host, then UTM,
then user-agent (llm-analytics.md section 4) — so the same event always
classifies the same way. Unmatched signals return ``None``; the caller maps
that to ``is_ai_referral=false, ai_source=other`` — the classifier never
guesses a source.
"""

from __future__ import annotations

from dataclasses import dataclass

# Suffix-safe host matching is OWNED by the analysis normalization module —
# reused, never reimplemented (invariant 2).
from app.analysis.normalization import domain_matches
from app.core.config.analytics import (
    AI_REFERRAL_HOST_RULES,
    AI_REFERRAL_UA_RULES,
    AI_REFERRAL_UTM_RULES,
    AI_SOURCE_TO_LOGICAL_ENGINE,
    MATCH_SIGNAL_REFERRER,
    MATCH_SIGNAL_USER_AGENT,
    MATCH_SIGNAL_UTM,
)


@dataclass(frozen=True)
class RuleMatch:
    """The one deterministic classification outcome for a signal set.

    Carries everything a ``ReferralClassification`` row needs beyond the
    versions (which the CALLER stamps — classifiers are pure): the detected
    ``ai_source``, the audited ``logical_engine`` join key when one exists
    (invariant 10), the config rule that fired, the signal tier it fired on,
    and the deterministic confidence bucket.
    """

    ai_source: str
    logical_engine: str | None
    matched_rule_id: str
    match_signal: str  # referrer | utm | user_agent
    confidence: str  # exact | heuristic


def _normalize_signal(value: str | None) -> str:
    return (value or "").strip().casefold()


def _rule_match(
    rule_id: str, ai_source: str, signal: str, confidence: str
) -> RuleMatch:
    return RuleMatch(
        ai_source=ai_source,
        logical_engine=AI_SOURCE_TO_LOGICAL_ENGINE.get(ai_source),
        matched_rule_id=rule_id,
        match_signal=signal,
        confidence=confidence,
    )


def _match_referrer_host(referrer_host: str | None) -> RuleMatch | None:
    host = _normalize_signal(referrer_host)
    if not host:
        return None
    for rule in AI_REFERRAL_HOST_RULES:
        # Boundary-safe: candidate must EQUAL the rule host or be a subdomain
        # of it — "notchatgpt.com" never matches "chatgpt.com".
        if domain_matches(host, rule.host):
            return _rule_match(
                rule.rule_id, rule.ai_source, MATCH_SIGNAL_REFERRER, rule.confidence
            )
    return None


def _match_utm(utm_source: str | None, utm_medium: str | None) -> RuleMatch | None:
    source = _normalize_signal(utm_source)
    medium = _normalize_signal(utm_medium)
    if not source and not medium:
        return None
    for rule in AI_REFERRAL_UTM_RULES:
        # Every constraint on the rule must equal the normalized signal.
        if rule.utm_source is not None and rule.utm_source != source:
            continue
        if rule.utm_medium is not None and rule.utm_medium != medium:
            continue
        return _rule_match(
            rule.rule_id, rule.ai_source, MATCH_SIGNAL_UTM, rule.confidence
        )
    return None


def _match_user_agent(user_agent: str | None) -> RuleMatch | None:
    ua = _normalize_signal(user_agent)
    if not ua:
        return None
    for rule in AI_REFERRAL_UA_RULES:
        if rule.substring in ua:
            return _rule_match(
                rule.rule_id,
                rule.ai_source,
                MATCH_SIGNAL_USER_AGENT,
                rule.confidence,
            )
    return None


def classify_referral_signals(
    referrer_host: str | None = None,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    user_agent: str | None = None,
) -> RuleMatch | None:
    """Classify one referral's signals against the config rule tables.

    Fixed priority: referrer host -> UTM -> user-agent; the first rule (in
    config order) to fire within a tier wins. Returns ``None`` when nothing
    matches (the caller records ``ai_source=other``); an absent/empty signal
    simply never fires its tier.
    """
    return (
        _match_referrer_host(referrer_host)
        or _match_utm(utm_source, utm_medium)
        or _match_user_agent(user_agent)
    )
