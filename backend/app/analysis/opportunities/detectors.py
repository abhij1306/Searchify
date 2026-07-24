# Opportunities deterministic detectors (pure — no DB, no I/O, invariants 7+9).
#
# Each detector is a pure function over typed, already-persisted evidence
# bundles (``VisibilityEvidence`` / ``SiteEvidence``) and returns
# ``DetectorHit`` projections. A hit carries the validated catalog
# ``rule_id``, the deterministic ``target_key`` identity, a JSON-ready
# evidence dict, the stringified source-row id lists (provenance, invariant
# 4), and the ``value_factor`` / ``gap_factor`` inputs the scoring formula
# turns into the priority. Detectors never score (``scoring.py`` owns the
# formula), never read the DB, and never call a provider or an LLM. A
# catalog-disabled rule is never emitted, even if a detector is invoked.
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.analysis.opportunities.scoring import (
    gap_factor_visibility,
    value_factor_for_intent,
)
from app.core.config.opportunities import (
    OPPORTUNITY_RULES_BY_ID,
    SITE_GAP_FACTOR,
    SITE_STRUCTURED_DATA_RULE_IDS,
    SITE_THIN_CONTENT_RULE_IDS,
    SITE_VALUE_FACTOR,
)

# Catalog rule ids this module emits (validated by the catalog lookup itself).
RULE_BRAND_ABSENT = "brand_absent_high_value_prompt"
RULE_OWNED_PAGE_NOT_CITED = "owned_page_not_cited"
RULE_MISSING_STRUCTURED_DATA = "missing_structured_data"
RULE_THIN_CONTENT = "thin_content"


# =========================================================================
# Typed evidence bundles (frozen input projections)
# =========================================================================
@dataclass(frozen=True)
class AnalysisEvidence:
    """One persisted ``ResponseAnalysis`` reduced to its citation signals."""

    analysis_id: uuid.UUID
    prompt_index: int
    logical_engine: str
    # Owned-classified citations on this repetition.
    owned_citation_count: int
    # Competitor citation-or-mention names on this repetition (deduped later).
    competitor_names: tuple[str, ...]


@dataclass(frozen=True)
class PromptSnapshotEvidence:
    """One frozen ``AuditPromptSnapshot`` (text/theme/intent + prompt link)."""

    prompt_index: int
    prompt_id: uuid.UUID | None
    text: str
    theme: str
    intent: str


@dataclass(frozen=True)
class VisibilityEvidence:
    """The visibility slice one audit offers the detectors."""

    audit_id: uuid.UUID
    analyses: tuple[AnalysisEvidence, ...]
    prompt_snapshots: tuple[PromptSnapshotEvidence, ...]
    owned_domains: tuple[str, ...]


@dataclass(frozen=True)
class SiteIssueEvidence:
    """One persisted ``SiteIssue`` for the selected crawl."""

    issue_id: uuid.UUID
    rule_id: str
    severity: str
    category: str
    site_url_id: uuid.UUID
    evidence: dict[str, Any] | None


@dataclass(frozen=True)
class SiteUrlEvidence:
    """The ``site_url_id -> normalized_url`` identity map entry."""

    site_url_id: uuid.UUID
    normalized_url: str


@dataclass(frozen=True)
class SiteEvidence:
    """The Site Health slice one crawl offers the detectors."""

    crawl_id: uuid.UUID
    issues: tuple[SiteIssueEvidence, ...]
    urls: tuple[SiteUrlEvidence, ...]


@dataclass(frozen=True)
class DetectorHit:
    """One deterministic rule firing on one target (JSON-ready projection)."""

    rule_id: str
    target_key: str
    target_prompt_id: uuid.UUID | None
    target_url: str | None
    target_theme: str | None
    evidence: dict[str, Any]
    # Provenance (stringified UUIDs, invariant 4) — at least one populated.
    source_analysis_ids: tuple[str, ...]
    source_issue_ids: tuple[str, ...]
    source_metric_ids: tuple[str, ...]
    value_factor: float
    gap_factor: float


# =========================================================================
# Visibility detectors
# =========================================================================
def _prompt_target(
    *,
    audit_id: uuid.UUID,
    snapshot: PromptSnapshotEvidence | None,
    prompt_index: int,
) -> tuple[str, uuid.UUID | None, str | None]:
    """Deterministic ``(target_key, target_prompt_id, target_theme)`` (D4).

    Prefers the live prompt identity; falls back to the frozen
    ``prompt-index:{audit_id}:{prompt_index}`` key when the prompt was deleted
    after the audit (the snapshot id is null), so the row stays valid.
    """
    theme = (snapshot.theme or None) if snapshot is not None else None
    if snapshot is not None and snapshot.prompt_id is not None:
        return f"prompt:{snapshot.prompt_id}", snapshot.prompt_id, theme
    return f"prompt-index:{audit_id}:{prompt_index}", None, theme


def _group_by_prompt_index(
    analyses: tuple[AnalysisEvidence, ...],
) -> dict[int, list[AnalysisEvidence]]:
    groups: dict[int, list[AnalysisEvidence]] = {}
    for analysis in analyses:
        groups.setdefault(analysis.prompt_index, []).append(analysis)
    return groups


def detect_brand_absent_high_value_prompt(
    evidence: VisibilityEvidence,
) -> list[DetectorHit]:
    """Fire per prompt with no owned citation and >=1 competitor present.

    Groups the audit's analyses by ``prompt_index``; a prompt fires when NO
    repetition carries an owned citation AND at least one competitor
    citation-or-mention appears across the repetitions.
    """
    rule = OPPORTUNITY_RULES_BY_ID[RULE_BRAND_ABSENT]
    if not rule.enabled:
        return []
    snapshots = {s.prompt_index: s for s in evidence.prompt_snapshots}
    groups = _group_by_prompt_index(evidence.analyses)
    hits: list[DetectorHit] = []
    for prompt_index in sorted(groups):
        analyses = groups[prompt_index]
        if any(a.owned_citation_count > 0 for a in analyses):
            continue
        competitor_names = sorted(
            {name for a in analyses for name in a.competitor_names if name}
        )
        if not competitor_names:
            continue
        snapshot = snapshots.get(prompt_index)
        target_key, prompt_id, theme = _prompt_target(
            audit_id=evidence.audit_id,
            snapshot=snapshot,
            prompt_index=prompt_index,
        )
        intent = snapshot.intent if snapshot is not None else ""
        hits.append(
            DetectorHit(
                rule_id=rule.rule_id,
                target_key=target_key,
                target_prompt_id=prompt_id,
                target_url=None,
                target_theme=theme,
                evidence={
                    "prompt_text": snapshot.text if snapshot is not None else "",
                    "prompt_intent": intent,
                    "prompt_theme": snapshot.theme if snapshot is not None else "",
                    "prompt_index": prompt_index,
                    "repetitions": len(analyses),
                    "owned_citation_count": 0,
                    "competitor_names": competitor_names,
                    "engines": sorted(
                        {a.logical_engine for a in analyses if a.logical_engine}
                    ),
                    "audit_id": str(evidence.audit_id),
                },
                source_analysis_ids=tuple(
                    str(a.analysis_id)
                    for a in sorted(analyses, key=lambda a: str(a.analysis_id))
                ),
                source_issue_ids=(),
                source_metric_ids=(),
                value_factor=value_factor_for_intent(intent),
                gap_factor=gap_factor_visibility(
                    competitor_count=len(competitor_names),
                    owned_citation_rate=0.0,
                ),
            )
        )
    return hits


def detect_owned_page_not_cited(evidence: VisibilityEvidence) -> list[DetectorHit]:
    """Fire per prompt with zero owned citations across all repetitions.

    Requires at least one owned domain: with nothing to cite the rule cannot
    fire and is skipped entirely.
    """
    rule = OPPORTUNITY_RULES_BY_ID[RULE_OWNED_PAGE_NOT_CITED]
    if not rule.enabled or not evidence.owned_domains:
        return []
    snapshots = {s.prompt_index: s for s in evidence.prompt_snapshots}
    groups = _group_by_prompt_index(evidence.analyses)
    hits: list[DetectorHit] = []
    for prompt_index in sorted(groups):
        analyses = groups[prompt_index]
        if any(a.owned_citation_count > 0 for a in analyses):
            continue
        competitor_names = sorted(
            {name for a in analyses for name in a.competitor_names if name}
        )
        snapshot = snapshots.get(prompt_index)
        target_key, prompt_id, theme = _prompt_target(
            audit_id=evidence.audit_id,
            snapshot=snapshot,
            prompt_index=prompt_index,
        )
        intent = snapshot.intent if snapshot is not None else ""
        hits.append(
            DetectorHit(
                rule_id=rule.rule_id,
                target_key=target_key,
                target_prompt_id=prompt_id,
                target_url=None,
                target_theme=theme,
                evidence={
                    "prompt_text": snapshot.text if snapshot is not None else "",
                    "prompt_intent": intent,
                    "prompt_theme": snapshot.theme if snapshot is not None else "",
                    "prompt_index": prompt_index,
                    "repetitions": len(analyses),
                    "owned_citation_count": 0,
                    "owned_domains": sorted(evidence.owned_domains),
                    "audit_id": str(evidence.audit_id),
                },
                source_analysis_ids=tuple(
                    str(a.analysis_id)
                    for a in sorted(analyses, key=lambda a: str(a.analysis_id))
                ),
                source_issue_ids=(),
                source_metric_ids=(),
                value_factor=value_factor_for_intent(intent),
                gap_factor=gap_factor_visibility(
                    competitor_count=len(competitor_names),
                    owned_citation_rate=0.0,
                ),
            )
        )
    return hits


# =========================================================================
# Site detectors (one hit per mapped SiteIssue)
# =========================================================================
def _site_opportunity_rule_id(issue_rule_id: str) -> str | None:
    """Map a persisted SiteIssue rule id to its opportunity rule (config)."""
    if issue_rule_id in SITE_STRUCTURED_DATA_RULE_IDS:
        return RULE_MISSING_STRUCTURED_DATA
    if issue_rule_id in SITE_THIN_CONTENT_RULE_IDS:
        return RULE_THIN_CONTENT
    return None


def detect_site_issue_opportunities(evidence: SiteEvidence) -> list[DetectorHit]:
    """Project each mapped ``SiteIssue`` into a site-type opportunity hit.

    One hit per issue whose ``rule_id`` is in the config mapping sets
    (``missing_structured_data`` / ``thin_content``), targeted at the issue's
    normalized URL. Issues whose URL identity is missing (cannot form the
    deterministic target key) are skipped.
    """
    urls = {u.site_url_id: u.normalized_url for u in evidence.urls}
    hits: list[DetectorHit] = []
    for issue in evidence.issues:
        rule_id = _site_opportunity_rule_id(issue.rule_id)
        if rule_id is None:
            continue
        rule = OPPORTUNITY_RULES_BY_ID[rule_id]
        if not rule.enabled:
            continue
        normalized_url = urls.get(issue.site_url_id)
        if not normalized_url:
            continue
        hits.append(
            DetectorHit(
                rule_id=rule.rule_id,
                target_key=f"url:{normalized_url}",
                target_prompt_id=None,
                target_url=normalized_url,
                target_theme=None,
                evidence={
                    "issue_rule_id": issue.rule_id,
                    "issue_severity": issue.severity,
                    "category": issue.category,
                    "issue_evidence": issue.evidence or {},
                    "crawl_id": str(evidence.crawl_id),
                    "url": normalized_url,
                },
                source_analysis_ids=(),
                source_issue_ids=(str(issue.issue_id),),
                source_metric_ids=(),
                value_factor=SITE_VALUE_FACTOR,
                gap_factor=SITE_GAP_FACTOR,
            )
        )
    hits.sort(key=lambda hit: (hit.rule_id, hit.target_key, hit.source_issue_ids))
    return hits
