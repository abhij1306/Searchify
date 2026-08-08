"""Assemble and persist one crawl's Site Intelligence projection.

Runs at crawl finalization, immediately after the knowledge build and before the
aggregate snapshot. Reads persisted rows, hands them to the PURE scorers in
``app.analysis.site_health.intelligence``, and stores the result on
``SiteHealthSnapshot`` — extending the existing crawl projection rather than
adding a parallel snapshot table (plan §10: "extend ``SiteHealthSnapshot`` into
the versioned Site Intelligence projection").

The stored payload is bounded and complete: coverage for ~29 questions, ~5
journey stages, and 6 dimensions is a few kilobytes, and storing it whole is
what lets every read endpoint render persisted state without recomputing a
score, re-resolving a pack, or touching the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.site_health.intelligence import (
    CorpusSignals,
    DimensionReport,
    JourneyReport,
    KnowledgeIndex,
    QuestionCoverageReport,
    resolve_journeys,
    resolve_question_coverage,
    score_dimensions,
)
from app.analysis.site_health.knowledge import KnowledgeVocabulary
from app.core.config.site_health import (
    CORPUS_DISPOSITION_ANALYZE,
    CORPUS_DISPOSITION_INVENTORY_ONLY,
    ITEM_KIND_DOCUMENT,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    TASK_KIND_ANALYZE,
)
from app.core.config.site_intelligence import (
    DIMENSION_FORMULA_VERSION,
    JOURNEY_COVERAGE_VERSION,
    KNOWLEDGE_EXTRACTOR_VERSION,
    OVERLAY_NOT_APPLICABLE_QUESTIONS_KEY,
    PROJECT_OVERLAY_KEY,
    QUESTION_COVERAGE_VERSION,
)
from app.core.config.task_queue import TASK_STATUS_FAILED
from app.domain.site_health.industry_pack import (
    frozen_manifest,
    knowledge_vocabulary_for_manifest,
)
from app.domain.site_health.knowledge import KnowledgeBuildResult
from app.models.knowledge import KnowledgeAssertion, KnowledgeEntity, KnowledgeRelation
from app.models.site_health import (
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SitePageAnalysis,
    SiteUrl,
    SiteUrlObservation,
)

__all__ = ["IntelligenceProjection", "build_intelligence_projection"]


@dataclass(frozen=True)
class IntelligenceProjection:
    """The bounded JSON payload stored on the snapshot and rendered by reads."""

    payload: dict
    packed: bool


async def build_intelligence_projection(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    knowledge_result: KnowledgeBuildResult,
) -> IntelligenceProjection:
    """Score coverage, journeys, and dimensions from this crawl's persisted rows."""

    manifest = frozen_manifest(crawl.configuration)
    vocabulary = knowledge_vocabulary_for_manifest(manifest)
    corpus = await _corpus_counts(session, crawl=crawl)
    if vocabulary is None or manifest is None:
        # No pack ever applied. There is no vocabulary in which a question,
        # journey, or knowledge dimension means anything here, so the projection
        # says exactly that instead of scoring a site against nothing.
        return IntelligenceProjection(
            payload={
                "packed": False,
                "reason": "no industry pack was resolved for this crawl",
                "corpus": corpus,
                "versions": _versions(),
            },
            packed=False,
        )

    knowledge = await _knowledge_index(session, crawl=crawl, result=knowledge_result)
    signals, observed_roles = await _corpus_signals(
        session, crawl=crawl, vocabulary=vocabulary
    )
    coverage = resolve_question_coverage(
        vocabulary=vocabulary,
        knowledge=knowledge,
        observed_role_ids=observed_roles,
        # ONLY when the crawl acquired nothing at all. A crawl-wide "some URL
        # failed" flag turned every unanswered question into
        # ``unavailable_evidence``, which says "we could not look" about pages
        # that were fetched perfectly well and simply do not answer. We cannot
        # know the role of a page that never analyzed, so the honest boundary is
        # total acquisition failure: then nothing about the site is being judged.
        acquisition_failed=(signals.analyzed_pages == 0 and signals.failed_pages > 0),
        not_applicable_question_ids=_overlay_not_applicable(crawl),
    )
    journeys = resolve_journeys(
        vocabulary=vocabulary, observed_role_ids=observed_roles, coverage=coverage
    )
    dimensions = score_dimensions(
        signals=signals,
        knowledge=knowledge,
        coverage=coverage,
        journeys=journeys,
        vocabulary=vocabulary,
    )

    return IntelligenceProjection(
        payload={
            "packed": True,
            "manifest": dict(manifest),
            "corpus": corpus,
            "knowledge": _knowledge_summary(knowledge_result, knowledge),
            "coverage": _coverage_payload(coverage),
            "journeys": _journeys_payload(journeys),
            "dimensions": _dimensions_payload(dimensions),
            "versions": _versions(),
        },
        packed=True,
    )


def _versions() -> dict[str, str]:
    return {
        "knowledge_extractor": KNOWLEDGE_EXTRACTOR_VERSION,
        "question_coverage": QUESTION_COVERAGE_VERSION,
        "journey_coverage": JOURNEY_COVERAGE_VERSION,
        "dimension_formula": DIMENSION_FORMULA_VERSION,
    }


def projection_version() -> str:
    """One identity for EVERY input that shapes a stored projection.

    Stamped on the snapshot so a version-filtered query can tell a stale
    projection from a current one. It used to be the dimension formula version
    alone, which meant a new knowledge extractor or coverage rule produced a
    materially different projection under an unchanged version stamp — and
    every such query then treated the stale rows as current.

    A digest rather than a join: the four component versions do not fit the
    column, and the snapshot payload already carries them verbatim under
    ``versions`` for anyone who needs to read which is which.
    """
    material = "|".join(f"{key}={value}" for key, value in sorted(_versions().items()))
    return f"si-{sha256(material.encode()).hexdigest()[:16]}"


def _overlay_not_applicable(crawl: SiteCrawl) -> frozenset[str]:
    """Questions a reviewer declared out of scope, frozen onto the crawl.

    Read from the crawl's own configuration, not from live project settings: a
    later overlay edit must not change what a past crawl reported, for exactly
    the reason the pack manifest is frozen there too.
    """
    overlay = (crawl.configuration or {}).get(PROJECT_OVERLAY_KEY)
    if not isinstance(overlay, Mapping):
        return frozenset()
    declared = overlay.get(OVERLAY_NOT_APPLICABLE_QUESTIONS_KEY)
    if not isinstance(declared, Sequence) or isinstance(declared, str):
        return frozenset()
    return frozenset(str(item) for item in declared if isinstance(item, str))


# =========================================================================
# Persisted-row reads
# =========================================================================
async def _corpus_counts(session: AsyncSession, *, crawl: SiteCrawl) -> dict:
    """Disposition and item-kind breakdown of everything the crawl discovered.

    Documents appear here as inventoried items. A prospectus PDF counts toward
    what the site publishes even though it never enters the HTML analyzer, which
    is the whole point of ``inventory_only``.

    Counted through THIS crawl's observations rather than off ``SiteUrl``, which
    is project-scoped: a URL discovered by an earlier crawl and gone by this one
    is part of the project's history, not of this crawl's corpus, and counting it
    here would make a shrinking site look unchanged.
    """
    rows = (
        await session.execute(
            select(
                SiteUrl.corpus_disposition,
                SiteUrl.item_kind,
                func.count().label("count"),
            )
            .join(SiteUrlObservation, SiteUrlObservation.site_url_id == SiteUrl.id)
            .where(SiteUrlObservation.crawl_id == crawl.id)
            .group_by(SiteUrl.corpus_disposition, SiteUrl.item_kind)
        )
    ).all()
    by_disposition: dict[str, int] = {}
    by_item_kind: dict[str, int] = {}
    for disposition, item_kind, count in rows:
        by_disposition[str(disposition or "")] = by_disposition.get(
            str(disposition or ""), 0
        ) + int(count)
        by_item_kind[str(item_kind or "")] = by_item_kind.get(
            str(item_kind or ""), 0
        ) + int(count)
    return {
        "by_disposition": by_disposition,
        "by_item_kind": by_item_kind,
        "discovered": sum(by_disposition.values()),
        "analyzable": by_disposition.get(CORPUS_DISPOSITION_ANALYZE, 0),
        "inventory_only": by_disposition.get(CORPUS_DISPOSITION_INVENTORY_ONLY, 0),
        "documents": by_item_kind.get(ITEM_KIND_DOCUMENT, 0),
    }


async def _knowledge_index(
    session: AsyncSession, *, crawl: SiteCrawl, result: KnowledgeBuildResult
) -> KnowledgeIndex:
    """Index the crawl's persisted assertions by predicate for coverage."""
    rows = (
        await session.execute(
            select(
                KnowledgeAssertion.predicate_id,
                KnowledgeAssertion.temporal_state,
                KnowledgeAssertion.contradiction_group_id,
            ).where(KnowledgeAssertion.crawl_id == crawl.id)
        )
    ).all()
    states: dict[str, set[str]] = {}
    disputed: set[str] = set()
    for predicate_id, temporal_state, group_id in rows:
        states.setdefault(str(predicate_id), set()).add(str(temporal_state or ""))
        if group_id is not None:
            disputed.add(str(predicate_id))

    entity_type_ids = set(
        (
            await session.execute(
                select(KnowledgeEntity.entity_type_id)
                .where(KnowledgeEntity.crawl_id == crawl.id)
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    relation_count = int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeRelation)
            .where(KnowledgeRelation.crawl_id == crawl.id)
        )
        or 0
    )
    return KnowledgeIndex(
        predicate_states={key: frozenset(value) for key, value in states.items()},
        disputed_predicates=frozenset(disputed),
        entity_type_ids=frozenset(str(item) for item in entity_type_ids),
        entity_count=result.entity_count,
        assertion_count=len(rows),
        relation_count=relation_count,
        contradiction_count=result.contradiction_count,
    )


async def _corpus_signals(
    session: AsyncSession, *, crawl: SiteCrawl, vocabulary: KnowledgeVocabulary
) -> tuple[CorpusSignals, frozenset[str]]:
    """Fold the crawl's current analyses into the dimension formula's inputs."""

    rows = (
        await session.execute(
            select(
                SitePageAnalysis.industry_role_id,
                SitePageAnalysis.secondary_role_ids,
                SiteFetchArtifact.final_url,
                SiteFetchArtifact.normalized_facts,
            )
            .join(
                SiteFetchArtifact,
                SiteFetchArtifact.id == SitePageAnalysis.artifact_id,
            )
            .where(
                SitePageAnalysis.crawl_id == crawl.id,
                SitePageAnalysis.status == PAGE_ANALYSIS_STATUS_COMPLETED,
                SitePageAnalysis.is_current.is_(True),
            )
        )
    ).all()

    failed = int(
        await session.scalar(
            select(func.count(func.distinct(SiteCrawlTask.url_hash))).where(
                SiteCrawlTask.crawl_id == crawl.id,
                SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                SiteCrawlTask.status == TASK_STATUS_FAILED,
            )
        )
        or 0
    )

    tally = _PageTally()
    observed_roles: set[str] = set()
    for row in rows:
        raw = row.normalized_facts
        facts = raw if isinstance(raw, Mapping) else {}
        # Every role the page carries, primary AND secondary. ``role_coverage``
        # and ``policy_role_pages`` are documented as primary-or-secondary, so
        # counting only the primary under-reported a page that legitimately
        # serves two roles — a fees page that is also a policy page stopped
        # counting toward policy evidence.
        page_roles = tuple(
            dict.fromkeys(
                role
                for role in (
                    str(row.industry_role_id or ""),
                    *(str(secondary) for secondary in row.secondary_role_ids or ()),
                )
                if role
            )
        )
        tally.add(facts, final_url=str(row.final_url or ""), role_ids=page_roles)
        observed_roles.update(page_roles)

    return (
        tally.to_signals(
            failed_pages=failed,
            declared_role_count=len(vocabulary.roles),
        ),
        frozenset(observed_roles),
    )


@dataclass
class _PageTally:
    """Accumulates per-page booleans into the counts the formula consumes."""

    analyzed: int = 0
    indexable: int = 0
    canonical_ok: int = 0
    linked: int = 0
    with_schema: int = 0
    with_valid_schema: int = 0
    with_schema_parity: int = 0
    with_author: int = 0
    with_dates: int = 0
    with_citation: int = 0
    with_question_headings: int = 0
    with_answer_first: int = 0
    with_usable_headings: int = 0
    with_conversion_action: int = 0
    role_pages: dict[str, int] = field(default_factory=dict)
    # role_id -> pages carrying that role that also link onward internally.
    role_continuity: dict[str, int] = field(default_factory=dict)

    def add(self, facts: Mapping, *, final_url: str, role_ids: Sequence[str]) -> None:
        self.analyzed += 1
        for role_id in role_ids:
            self.role_pages[role_id] = self.role_pages.get(role_id, 0) + 1
        self._add_delivery(facts, final_url=final_url, role_ids=role_ids)
        self._add_schema(facts)
        self._add_trust(facts)
        self._add_answerability(facts)

    def _add_delivery(
        self, facts: Mapping, *, final_url: str, role_ids: Sequence[str]
    ) -> None:
        if not (facts.get("robots") or {}).get("noindex"):
            self.indexable += 1
        canonical = str(facts.get("canonical_url") or "")
        # A page with no canonical declaration is not broken; a page that
        # declares one pointing elsewhere is the integrity failure.
        if not canonical or canonical.rstrip("/") == final_url.rstrip("/"):
            self.canonical_ok += 1
        anchors = (facts.get("links") or {}).get("anchors") or ()
        if any(
            isinstance(anchor, Mapping) and anchor.get("is_internal")
            for anchor in anchors
        ):
            self.linked += 1
            # Continuity is counted on THIS page's own anchors. Deriving it from
            # a crawl-wide total was a fabricated number: it reported that every
            # role linked onward whenever any page did.
            for role_id in role_ids:
                self.role_continuity[role_id] = self.role_continuity.get(role_id, 0) + 1

    def _add_schema(self, facts: Mapping) -> None:
        structured = facts.get("structured_data") or {}
        blocks = [b for b in structured.get("blocks") or () if isinstance(b, Mapping)]
        if not blocks:
            return
        self.with_schema += 1
        if all(block.get("valid") for block in blocks):
            self.with_valid_schema += 1
        if self._schema_matches_visible(blocks, facts):
            self.with_schema_parity += 1

    def _add_trust(self, facts: Mapping) -> None:
        if str(facts.get("author") or ""):
            self.with_author += 1
        dates = facts.get("dates") or {}
        if dates.get("published") or dates.get("modified"):
            self.with_dates += 1
        if facts.get("outbound_domains"):
            self.with_citation += 1

    def _add_answerability(self, facts: Mapping) -> None:
        if float(facts.get("question_heading_ratio") or 0.0) > 0:
            self.with_question_headings += 1
        if str(facts.get("first_answer_text") or ""):
            self.with_answer_first += 1
        headings = facts.get("headings") or {}
        if int(headings.get("h1_count") or 0) == 1 and (headings.get("h2_texts") or ()):
            self.with_usable_headings += 1
        if facts.get("cta_text") or facts.get("form_fields"):
            self.with_conversion_action += 1

    @staticmethod
    def _schema_matches_visible(blocks: Sequence[Mapping], facts: Mapping) -> bool:
        """Whether a schema name also appears in the page's visible content.

        Parity is checked against the TITLE and H1 because those are what a
        reader sees first. A schema block naming something the page never shows
        is the mismatch this measures — structured data that describes a
        different page than the one served.
        """
        visible = " ".join(
            [
                str(facts.get("title") or ""),
                *(
                    str(text)
                    for text in (facts.get("headings") or {}).get("h1_texts") or ()
                ),
            ]
        ).casefold()
        named = [str(block.get("name") or "").casefold() for block in blocks]
        named = [name for name in named if name]
        if not named:
            # No name to compare. Absence of a claim is not a mismatch.
            return True
        if not visible.strip():
            # The schema names something and the page shows nothing to match it
            # against — the mismatch this measures, at its most extreme. It used
            # to score as parity: ``visible in name`` is ``"" in name``, which
            # is true for every name, so a page with no title and no H1 passed
            # against any structured data at all.
            return False
        return any(name in visible or visible in name for name in named)

    def to_signals(
        self, *, failed_pages: int, declared_role_count: int
    ) -> CorpusSignals:
        return CorpusSignals(
            analyzed_pages=self.analyzed,
            failed_pages=failed_pages,
            indexable_pages=self.indexable,
            canonical_ok_pages=self.canonical_ok,
            linked_pages=self.linked,
            pages_with_schema=self.with_schema,
            pages_with_valid_schema=self.with_valid_schema,
            pages_with_schema_parity=self.with_schema_parity,
            pages_with_author=self.with_author,
            pages_with_dates=self.with_dates,
            pages_with_outbound_citation=self.with_citation,
            pages_with_question_headings=self.with_question_headings,
            pages_with_answer_first=self.with_answer_first,
            pages_with_usable_headings=self.with_usable_headings,
            conversion_action_pages=self.with_conversion_action,
            role_page_counts=dict(self.role_pages),
            # A stage page that links nowhere onward is where a journey stops.
            role_continuity_counts=dict(self.role_continuity),
            declared_role_count=declared_role_count,
            # Entity consistency compares a page's schema-declared names against
            # the canonical entity, which this crawl-wide pass does not have in
            # scope. Reported as UNAVAILABLE rather than derived from schema
            # parity: parity answers "does the schema match the visible page",
            # a different question, and reusing it would publish a number
            # measuring something other than what its label claims.
            pages_with_entity_names=0,
            entity_name_conflicts=0,
            policy_role_pages=sum(
                count
                for role_id, count in self.role_pages.items()
                if "policy" in role_id or "disclosure" in role_id
            ),
        )


# =========================================================================
# Payload shaping
# =========================================================================
def _knowledge_summary(result: KnowledgeBuildResult, index: KnowledgeIndex) -> dict:
    return {
        "entity_count": result.entity_count,
        "assertion_count": index.assertion_count,
        "relation_count": index.relation_count,
        "contradiction_count": result.contradiction_count,
        "pages_considered": result.pages_considered,
        "pages_contributing": result.pages_contributing,
        "entity_type_ids": sorted(index.entity_type_ids),
        "warnings": list(result.warnings),
    }


def _coverage_payload(report: QuestionCoverageReport) -> dict:
    return {
        "answered_ratio": report.answered_ratio,
        "denominator": report.denominator,
        "counts": dict(report.counts),
        "questions": [asdict(question) for question in report.questions],
    }


def _journeys_payload(journeys: Sequence[JourneyReport]) -> list[dict]:
    return [asdict(journey) for journey in journeys]


def _dimensions_payload(report: DimensionReport) -> dict:
    return {
        "composite_score": report.composite_score,
        "composite_coverage": report.composite_coverage,
        "dimensions": [asdict(dimension) for dimension in report.dimensions],
    }
