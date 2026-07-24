"""Opportunities detectors: table-driven per-rule firing + edge cases."""

from __future__ import annotations

import uuid

import pytest

from app.analysis.opportunities.detectors import (
    AnalysisEvidence,
    PromptSnapshotEvidence,
    SiteEvidence,
    SiteIssueEvidence,
    SiteUrlEvidence,
    VisibilityEvidence,
    detect_brand_absent_high_value_prompt,
    detect_owned_page_not_cited,
    detect_site_issue_opportunities,
)
from app.core.config.opportunities import (
    OPPORTUNITY_RULES_BY_ID,
    SITE_GAP_FACTOR,
    SITE_VALUE_FACTOR,
)


def _analysis(
    prompt_index: int,
    *,
    owned: int = 0,
    competitors: tuple[str, ...] = (),
    engine: str = "gemini",
) -> AnalysisEvidence:
    return AnalysisEvidence(
        analysis_id=uuid.uuid4(),
        prompt_index=prompt_index,
        logical_engine=engine,
        owned_citation_count=owned,
        competitor_names=competitors,
    )


def _snapshot(
    prompt_index: int,
    *,
    prompt_id: uuid.UUID | None = None,
    text: str = "best payroll software",
    theme: str = "payroll",
    intent: str = "comparison",
) -> PromptSnapshotEvidence:
    return PromptSnapshotEvidence(
        prompt_index=prompt_index,
        prompt_id=prompt_id if prompt_id is not None else uuid.uuid4(),
        text=text,
        theme=theme,
        intent=intent,
    )


def _visibility(
    analyses: tuple[AnalysisEvidence, ...],
    snapshots: tuple[PromptSnapshotEvidence, ...] = (),
    owned_domains: tuple[str, ...] = ("acme.com",),
) -> VisibilityEvidence:
    return VisibilityEvidence(
        audit_id=uuid.uuid4(),
        analyses=analyses,
        prompt_snapshots=snapshots,
        owned_domains=owned_domains,
    )


# =========================================================================
# brand_absent_high_value_prompt
# =========================================================================
def test_brand_absent_fires_without_owned_and_with_competitor() -> None:
    evidence = _visibility(
        (
            _analysis(0, competitors=("Globex",)),
            _analysis(0, competitors=("Globex", "Initech"), engine="chatgpt"),
        ),
        (_snapshot(0),),
    )
    hits = detect_brand_absent_high_value_prompt(evidence)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.rule_id == "brand_absent_high_value_prompt"
    assert hit.evidence["repetitions"] == 2
    assert hit.evidence["competitor_names"] == ["Globex", "Initech"]
    assert hit.evidence["engines"] == ["chatgpt", "gemini"]
    assert hit.evidence["prompt_text"] == "best payroll software"
    assert hit.evidence["prompt_intent"] == "comparison"
    assert hit.evidence["prompt_theme"] == "payroll"
    assert hit.value_factor > 1.0  # comparison intent is weighted above base
    assert hit.gap_factor > 1.0  # competitors present, zero owned rate
    assert len(hit.source_analysis_ids) == 2
    assert hit.source_issue_ids == ()


@pytest.mark.parametrize(
    "analyses",
    [
        # An owned citation in ANY repetition suppresses the rule.
        (_analysis(0, owned=1, competitors=("Globex",)), _analysis(0)),
        # No competitor citation-or-mention anywhere -> no gap to close.
        (_analysis(0), _analysis(0, engine="chatgpt")),
    ],
)
def test_brand_absent_does_not_fire(analyses: tuple[AnalysisEvidence, ...]) -> None:
    evidence = _visibility(analyses, (_snapshot(0),))
    assert detect_brand_absent_high_value_prompt(evidence) == []


def test_brand_absent_empty_evidence_yields_no_hits() -> None:
    assert detect_brand_absent_high_value_prompt(_visibility(())) == []


def test_brand_absent_target_key_prefers_prompt_id() -> None:
    prompt_id = uuid.uuid4()
    evidence = _visibility(
        (_analysis(0, competitors=("Globex",)),),
        (_snapshot(0, prompt_id=prompt_id),),
    )
    (hit,) = detect_brand_absent_high_value_prompt(evidence)
    assert hit.target_key == f"prompt:{prompt_id}"
    assert hit.target_prompt_id == prompt_id
    assert hit.target_theme == "payroll"


def test_brand_absent_target_key_falls_back_to_prompt_index() -> None:
    evidence = _visibility(
        (_analysis(3, competitors=("Globex",)),),
        (_snapshot(3, prompt_id=None),),
    )
    # ``_snapshot`` mints an id when not told otherwise; force the null link.
    snapshots = (
        PromptSnapshotEvidence(
            prompt_index=3, prompt_id=None, text="t", theme="", intent=""
        ),
    )
    evidence = _visibility(
        (_analysis(3, competitors=("Globex",)),), snapshots
    )
    (hit,) = detect_brand_absent_high_value_prompt(evidence)
    assert hit.target_key == f"prompt-index:{evidence.audit_id}:3"
    assert hit.target_prompt_id is None
    assert hit.target_theme is None
    # Unknown/empty intent -> default value factor.
    assert hit.value_factor == 1.0


def test_brand_absent_missing_snapshot_still_fires() -> None:
    evidence = _visibility((_analysis(1, competitors=("Globex",)),), ())
    (hit,) = detect_brand_absent_high_value_prompt(evidence)
    assert hit.target_key == f"prompt-index:{evidence.audit_id}:1"
    assert hit.evidence["prompt_text"] == ""
    assert hit.evidence["prompt_intent"] == ""


def test_brand_absent_groups_by_prompt_index() -> None:
    evidence = _visibility(
        (
            _analysis(0, competitors=("Globex",)),
            _analysis(1, competitors=("Initech",)),
            _analysis(2, owned=2, competitors=("Globex",)),
        ),
        (_snapshot(0), _snapshot(1), _snapshot(2)),
    )
    hits = detect_brand_absent_high_value_prompt(evidence)
    assert len(hits) == 2
    assert [h.evidence["prompt_index"] for h in hits] == [0, 1]


# =========================================================================
# owned_page_not_cited
# =========================================================================
def test_owned_page_not_cited_fires_with_zero_owned_citations() -> None:
    evidence = _visibility((_analysis(0),), (_snapshot(0),))
    hits = detect_owned_page_not_cited(evidence)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.rule_id == "owned_page_not_cited"
    assert hit.evidence["owned_domains"] == ["acme.com"]
    assert hit.evidence["repetitions"] == 1
    assert hit.evidence["owned_citation_count"] == 0
    assert len(hit.source_analysis_ids) == 1


def test_owned_page_not_cited_skipped_without_owned_domains() -> None:
    evidence = _visibility(
        (_analysis(0),), (_snapshot(0),), owned_domains=()
    )
    assert detect_owned_page_not_cited(evidence) == []


def test_owned_page_not_cited_suppressed_by_any_owned_citation() -> None:
    evidence = _visibility(
        (_analysis(0), _analysis(0, owned=1, engine="chatgpt")),
        (_snapshot(0),),
    )
    assert detect_owned_page_not_cited(evidence) == []


def test_owned_page_not_cited_empty_evidence_yields_no_hits() -> None:
    assert detect_owned_page_not_cited(_visibility(())) == []


# =========================================================================
# Site rules (missing_structured_data / thin_content)
# =========================================================================
def _site(
    issues: tuple[SiteIssueEvidence, ...],
    urls: tuple[SiteUrlEvidence, ...],
) -> SiteEvidence:
    return SiteEvidence(crawl_id=uuid.uuid4(), issues=issues, urls=urls)


def _issue(
    rule_id: str,
    site_url_id: uuid.UUID,
    *,
    severity: str = "medium",
    category: str = "structured_data",
) -> SiteIssueEvidence:
    return SiteIssueEvidence(
        issue_id=uuid.uuid4(),
        rule_id=rule_id,
        severity=severity,
        category=category,
        site_url_id=site_url_id,
        evidence={"detail": "x"},
    )


def _url(site_url_id: uuid.UUID, normalized_url: str) -> SiteUrlEvidence:
    return SiteUrlEvidence(site_url_id=site_url_id, normalized_url=normalized_url)


def test_site_rules_fire_from_mapped_issues() -> None:
    url_id = uuid.uuid4()
    evidence = _site(
        (
            _issue("aeo.structured_data_present", url_id),
            _issue(
                "aeo.sufficient_text",
                url_id,
                severity="medium",
                category="content",
            ),
            _issue("aeo.open_graph_present", url_id),  # not mapped -> ignored
        ),
        (_url(url_id, "https://acme.com/pricing"),),
    )
    hits = detect_site_issue_opportunities(evidence)
    assert [h.rule_id for h in hits] == ["missing_structured_data", "thin_content"]
    for hit in hits:
        assert hit.target_key == "url:https://acme.com/pricing"
        assert hit.target_url == "https://acme.com/pricing"
        assert hit.target_prompt_id is None
        assert hit.value_factor == SITE_VALUE_FACTOR
        assert hit.gap_factor == SITE_GAP_FACTOR
        assert len(hit.source_issue_ids) == 1
        assert hit.source_analysis_ids == ()
        assert hit.evidence["crawl_id"] == str(evidence.crawl_id)
        assert hit.evidence["url"] == "https://acme.com/pricing"
        assert hit.evidence["issue_evidence"] == {"detail": "x"}


def test_site_rules_skip_issue_with_unknown_url_identity() -> None:
    evidence = _site(
        (_issue("aeo.structured_data_present", uuid.uuid4()),),
        (),  # no URL map entries
    )
    assert detect_site_issue_opportunities(evidence) == []


def test_site_rules_empty_evidence_yields_no_hits() -> None:
    assert detect_site_issue_opportunities(_site((), ())) == []


# =========================================================================
# Disabled rules are never emitted
# =========================================================================
@pytest.mark.parametrize(
    "rule_id,detector,evidence",
    [
        (
            "brand_absent_high_value_prompt",
            detect_brand_absent_high_value_prompt,
            _visibility(
                (_analysis(0, competitors=("Globex",)),), (_snapshot(0),)
            ),
        ),
        (
            "owned_page_not_cited",
            detect_owned_page_not_cited,
            _visibility((_analysis(0),), (_snapshot(0),)),
        ),
        (
            "missing_structured_data",
            detect_site_issue_opportunities,
            (lambda url_id: _site(
                (_issue("aeo.structured_data_present", url_id),),
                (_url(url_id, "https://acme.com/x"),),
            ))(uuid.uuid4()),
        ),
        (
            "thin_content",
            detect_site_issue_opportunities,
            (lambda url_id: _site(
                (_issue("aeo.sufficient_text", url_id, category="content"),),
                (_url(url_id, "https://acme.com/x"),),
            ))(uuid.uuid4()),
        ),
    ],
)
def test_disabled_rules_never_emit(monkeypatch, rule_id, detector, evidence) -> None:
    rule = OPPORTUNITY_RULES_BY_ID[rule_id]
    assert detector(evidence), "sanity: the detector fires while enabled"
    monkeypatch.setattr(rule, "enabled", False)
    assert detector(evidence) == []
