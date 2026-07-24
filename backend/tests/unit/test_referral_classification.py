"""Deterministic AI-referral classifier (A4): host/UTM/UA tiers, the fixed
referrer -> utm -> user_agent priority, boundary-safe host matching,
logical-engine mapping, and unmatched -> None (the caller records
``ai_source=other``). Pure — no DB, no network, no LLM (invariant 9)."""

from __future__ import annotations

from app.domain.analytics.classification import (
    RuleMatch,
    classify_referral_signals,
)


def test_referrer_host_exact_match() -> None:
    match = classify_referral_signals(referrer_host="chatgpt.com")
    assert match == RuleMatch(
        ai_source="chatgpt",
        logical_engine="chatgpt",
        matched_rule_id="host-chatgpt-com",
        match_signal="referrer",
        confidence="exact",
    )


def test_referrer_host_subdomain_and_case_match() -> None:
    # Suffix-safe: subdomains of a listed host match, casing irrelevant.
    for host in ("www.chatgpt.com", "CHAT.OPENAI.COM", " chat.openai.com "):
        match = classify_referral_signals(referrer_host=host)
        assert match is not None
        assert match.ai_source == "chatgpt"
        assert match.match_signal == "referrer"


def test_referrer_host_boundary_safety_no_substring_false_positives() -> None:
    # "notchatgpt.com" is neither equal to nor a subdomain of "chatgpt.com";
    # "chatgpt.com.evil.test" merely embeds the host as a prefix label.
    for host in ("notchatgpt.com", "chatgpt.com.evil.test", "openai.com"):
        assert classify_referral_signals(referrer_host=host) is None


def test_each_known_host_maps_to_its_source_and_engine() -> None:
    expected = {
        "gemini.google.com": ("gemini", "gemini"),
        "claude.ai": ("claude", "claude"),
        "perplexity.ai": ("perplexity", None),
        "copilot.microsoft.com": ("copilot", None),
    }
    for host, (ai_source, logical_engine) in expected.items():
        match = classify_referral_signals(referrer_host=host)
        assert match is not None
        assert match.ai_source == ai_source
        assert match.logical_engine == logical_engine
        assert match.confidence == "exact"


def test_priority_referrer_beats_utm_beats_user_agent() -> None:
    # All three tiers would fire; the referrer tier wins.
    match = classify_referral_signals(
        referrer_host="perplexity.ai",
        utm_source="chatgpt.com",
        utm_medium="referral",
        user_agent="ChatGPT-User/1.0",
    )
    assert match is not None
    assert match.match_signal == "referrer"
    assert match.ai_source == "perplexity"
    # Referrer absent -> UTM beats the user-agent tier.
    match = classify_referral_signals(
        referrer_host="example.com",
        utm_source="copilot",
        user_agent="Claude-User/1.0",
    )
    assert match is not None
    assert match.match_signal == "utm"
    assert match.ai_source == "copilot"


def test_utm_source_equality_is_normalized() -> None:
    match = classify_referral_signals(utm_source="  Perplexity.AI ")
    assert match is not None
    assert match.ai_source == "perplexity"
    assert match.match_signal == "utm"
    assert match.confidence == "exact"
    # A near-miss is not an equality match.
    assert classify_referral_signals(utm_source="notchatgpt") is None
    assert classify_referral_signals(utm_medium="referral") is None


def test_utm_google_ai_overview_has_no_logical_engine() -> None:
    match = classify_referral_signals(utm_source="google_ai_overview")
    assert match is not None
    assert match.ai_source == "google_ai_overview"
    assert match.logical_engine is None


def test_user_agent_substring_tier_is_heuristic() -> None:
    # Full fingerprintable UA strings and pre-reduced family tokens both hit
    # the casefolded substring rule.
    for ua in (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X) ChatGPT-User/1.0",
        "chatgpt-user",
    ):
        match = classify_referral_signals(user_agent=ua)
        assert match is not None
        assert match.ai_source == "chatgpt"
        assert match.logical_engine == "chatgpt"
        assert match.match_signal == "user_agent"
        assert match.confidence == "heuristic"


def test_unmatched_and_absent_signals_return_none() -> None:
    assert classify_referral_signals() is None
    assert classify_referral_signals(referrer_host="", utm_source="") is None
    assert (
        classify_referral_signals(
            referrer_host="google.com",
            utm_source="google",
            utm_medium="organic",
            user_agent="Mozilla/5.0",
        )
        is None
    )


def test_classification_is_deterministic() -> None:
    kwargs = {
        "referrer_host": "WWW.ChatGPT.com",
        "utm_source": "perplexity",
        "user_agent": "Claude-User",
    }
    assert classify_referral_signals(**kwargs) == classify_referral_signals(**kwargs)
