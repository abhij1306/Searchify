"""Build one crawl's typed knowledge from its persisted page evidence.

Runs at crawl finalization, after every analysis has terminalized and before the
snapshot. It is the only writer of ``knowledge_entities`` /
``knowledge_assertions`` / ``knowledge_relations``.

Why finalization and not per page: a contradiction is by definition cross-page,
so it cannot be detected while pages are still arriving. The pure extractor runs
per page (from each artifact's already-persisted bounded facts — no refetch),
and this module does the part that needs the whole corpus at once: merging
entity identity, deduplicating claims, and grouping conflicts.

Deterministic end to end. Row IDs are uuid5 over the crawl plus the row's
natural key, so replaying the same artifacts under the same versions writes
byte-identical knowledge — which is both the S2 gate and what makes the whole
build idempotent under ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.site_health.knowledge import (
    AssertionCandidate,
    EntityCandidate,
    EntityRef,
    KnowledgeVocabulary,
    PageKnowledge,
    RelationCandidate,
    extract_page_knowledge,
    identity_key_for,
    resolve_temporal_state,
)
from app.core.config.site_health import (
    PAGE_ANALYSIS_STATUS_COMPLETED,
    TEMPORAL_STATE_CURRENT,
    TEMPORAL_STATE_HISTORICAL,
)
from app.core.config.site_intelligence import (
    KNOWLEDGE_EXTRACTOR_VERSION,
    MAX_ASSERTIONS_PER_CRAWL,
    MAX_ENTITIES_PER_CRAWL,
    MAX_EVIDENCE_REFS_PER_ROW,
    MAX_RELATIONS_PER_CRAWL,
    MULTI_VALUE_CARDINALITIES,
    NON_CONFLICTING_POLICIES,
    REVIEW_STATE_OBSERVED,
)
from app.domain.site_health.industry_pack import (
    frozen_manifest,
    knowledge_vocabulary_for_manifest,
)
from app.models.knowledge import (
    KnowledgeAssertion,
    KnowledgeEntity,
    KnowledgeRelation,
    assertion_id,
    contradiction_group_id,
    entity_id,
    relation_id,
)
from app.models.site_health import SiteCrawl, SiteFetchArtifact, SitePageAnalysis

logger = logging.getLogger("app.domain.site_health.knowledge")

__all__ = ["KnowledgeBuildResult", "build_crawl_knowledge"]

SOURCE_KIND_FETCH_ARTIFACT = "site_fetch_artifact"


@dataclass(frozen=True)
class KnowledgeBuildResult:
    """What one build produced, for the snapshot and the report."""

    entity_count: int = 0
    assertion_count: int = 0
    relation_count: int = 0
    contradiction_count: int = 0
    pages_considered: int = 0
    pages_contributing: int = 0
    warnings: tuple[str, ...] = ()
    extractor_version: str = KNOWLEDGE_EXTRACTOR_VERSION
    # ``False`` means no pack ever applied — NOT that the site published
    # nothing. Readers must render those differently.
    packed: bool = False


@dataclass
class _PageInput:
    """One analyzed page's persisted evidence, as the extractor needs it."""

    analysis_id: uuid.UUID
    artifact_id: uuid.UUID
    site_url_id: uuid.UUID
    final_url: str
    content_hash: str
    facts: Mapping
    industry_role_id: str | None
    temporal_state: str
    is_crawl_root: bool


async def build_crawl_knowledge(
    session: AsyncSession, *, crawl: SiteCrawl, now: datetime | None = None
) -> KnowledgeBuildResult:
    """Derive and persist one crawl's entities, assertions, and relations."""

    manifest = frozen_manifest(crawl.configuration)
    vocabulary = knowledge_vocabulary_for_manifest(manifest)
    if vocabulary is None or manifest is None:
        # Unpacked crawl: nothing defined what an entity or a predicate would
        # mean here. Producing zero rows and saying so is the honest outcome.
        return KnowledgeBuildResult(packed=False)

    pages = await _load_pages(session, crawl=crawl)
    if not pages:
        return KnowledgeBuildResult(packed=True, warnings=("no_analyzed_pages",))

    if vocabulary.unusable_predicate_ids:
        logger.warning(
            "industry pack declares predicates with no subject entity type",
            extra={
                "pack_id": vocabulary.pack_id,
                "pack_version": vocabulary.pack_version,
                "predicate_ids": list(vocabulary.unusable_predicate_ids)[:20],
            },
        )
    site_key = _site_identity_key(crawl, pages)
    per_page = _extract_all(pages, vocabulary=vocabulary, site_identity_key=site_key)
    merged = _merge(per_page, vocabulary=vocabulary, now=now or datetime.now(UTC))

    await _persist(
        session,
        crawl=crawl,
        manifest=manifest,
        merged=merged,
    )
    return KnowledgeBuildResult(
        # A pack defect is reported beside the site's own gaps so a reader can
        # tell "we could not ask this" from "the site does not say".
        entity_count=len(merged.entities),
        assertion_count=len(merged.assertions),
        relation_count=len(merged.relations),
        contradiction_count=merged.contradiction_count,
        pages_considered=len(pages),
        pages_contributing=merged.pages_contributing,
        warnings=tuple(
            dict.fromkeys(
                (
                    *merged.warnings,
                    *(
                        ("pack_predicates_without_a_subject_entity_type",)
                        if vocabulary.unusable_predicate_ids
                        else ()
                    ),
                )
            )
        ),
        packed=True,
    )


# =========================================================================
# Load
# =========================================================================
async def _load_pages(session: AsyncSession, *, crawl: SiteCrawl) -> list[_PageInput]:
    """The crawl's CURRENT completed analyses joined to their artifacts.

    Reads ``is_current`` rows only: a superseded analysis describes the same
    page under an older pack, and folding both into one knowledge model would
    manufacture contradictions out of a version upgrade.
    """

    rows = (
        await session.execute(
            select(
                SitePageAnalysis.id,
                SitePageAnalysis.artifact_id,
                SitePageAnalysis.site_url_id,
                SitePageAnalysis.industry_role_id,
                SitePageAnalysis.temporal_state,
                SiteFetchArtifact.final_url,
                SiteFetchArtifact.content_hash,
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
            .order_by(SitePageAnalysis.created_at, SitePageAnalysis.id)
        )
    ).all()

    pages: list[_PageInput] = []
    for row in rows:
        if not isinstance(row.normalized_facts, Mapping):
            continue
        pages.append(
            _PageInput(
                analysis_id=row.id,
                artifact_id=row.artifact_id,
                site_url_id=row.site_url_id,
                final_url=str(row.final_url or ""),
                content_hash=str(row.content_hash or ""),
                facts=row.normalized_facts,
                industry_role_id=row.industry_role_id,
                temporal_state=str(row.temporal_state or ""),
                is_crawl_root=False,
            )
        )
    _mark_root(pages, crawl=crawl)
    return pages


def _mark_root(pages: list[_PageInput], *, crawl: SiteCrawl) -> None:
    """Flag the one page that may name the organization.

    Prefers the crawl's recorded root URL; falls back to the shortest path,
    which is the site root on every ordinary site. Exactly one page is marked —
    letting several claim it would let a section page rename the business.
    """
    root_url = str(getattr(crawl, "root_url", "") or "")
    for page in pages:
        if root_url and page.final_url.rstrip("/") == root_url.rstrip("/"):
            page.is_crawl_root = True
            return
    shortest = min(
        pages,
        key=lambda page: (len(page.final_url.rstrip("/")), page.final_url),
        default=None,
    )
    if shortest is not None:
        shortest.is_crawl_root = True


def _site_identity_key(crawl: SiteCrawl, pages: Sequence[_PageInput]) -> str:
    """The crawl-stable organization key every assertion attaches to.

    Built from the registrable domain rather than the observed name: the name is
    a FACT about the organization and can be absent, misspelled, or change
    between crawls, while the domain is the identity the project was created
    around. Keying on the name would make a retitled homepage look like a
    different business and orphan every prior assertion.
    """
    domain = str((crawl.configuration or {}).get("root_registrable_domain") or "")
    return identity_key_for(domain or _fallback_domain(crawl, pages))


def _fallback_domain(crawl: SiteCrawl, pages: Sequence[_PageInput]) -> str:
    """The host to key on when the crawl recorded no registrable domain.

    A HOST, never a full URL. Keying on a page's ``final_url`` carried its path
    and query, so two crawls that analyzed the same site in a different order
    produced different organization identities and orphaned every prior
    assertion. The crawl's own root URL is preferred; the page MARKED as the
    crawl root is the last resort — never merely the first row by ``created_at``.
    """
    root = str(getattr(crawl, "root_url", "") or "")
    if not root:
        root = next((page.final_url for page in pages if page.is_crawl_root), "")
    try:
        return (urlsplit(root).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


# =========================================================================
# Extract + merge
# =========================================================================
@dataclass
class _Merged:
    entities: dict[EntityRef, _MergedEntity]
    assertions: dict[tuple, _MergedAssertion]
    relations: dict[tuple, _MergedRelation]
    contradiction_count: int
    warnings: tuple[str, ...]
    pages_contributing: int


@dataclass
class _MergedEntity:
    candidate: EntityCandidate
    evidence: list[dict]
    page_ids: set[uuid.UUID]


@dataclass
class _MergedAssertion:
    candidate: AssertionCandidate
    evidence: list[dict]
    # Set by ``_group_contradictions`` when another value disputes this claim.
    disputed: bool = False


@dataclass
class _MergedRelation:
    candidate: RelationCandidate
    evidence: list[dict]


def _evidence_ref(page: _PageInput) -> dict:
    """One ``KnowledgeSourceRef``: the source ID is the authority, not the text."""
    return {
        "source_kind": SOURCE_KIND_FETCH_ARTIFACT,
        "source_id": str(page.artifact_id),
        "locator": {
            "url": page.final_url[:2048],
            "content_hash": page.content_hash,
            "site_url_id": str(page.site_url_id),
        },
    }


def _extract_all(
    pages: Sequence[_PageInput],
    *,
    vocabulary: KnowledgeVocabulary,
    site_identity_key: str,
) -> list[tuple[_PageInput, PageKnowledge]]:
    results: list[tuple[_PageInput, PageKnowledge]] = []
    for page in pages:
        knowledge = extract_page_knowledge(
            page.facts,
            vocabulary=vocabulary,
            industry_role_id=page.industry_role_id,
            temporal_state=page.temporal_state,
            site_identity_key=site_identity_key,
            is_crawl_root=page.is_crawl_root,
            final_url=page.final_url,
        )
        results.append((page, knowledge))
    return results


def _merge(
    per_page: Sequence[tuple[_PageInput, PageKnowledge]],
    *,
    vocabulary: KnowledgeVocabulary,
    now: datetime,
) -> _Merged:
    """Fold per-page candidates into one knowledge model and group conflicts."""

    entities: dict[EntityRef, _MergedEntity] = {}
    assertions: dict[tuple, _MergedAssertion] = {}
    relations: dict[tuple, _MergedRelation] = {}
    warnings: list[str] = []
    contributing = 0

    for page, knowledge in per_page:
        if knowledge.entities or knowledge.assertions:
            contributing += 1
        warnings.extend(knowledge.warnings)
        ref = _evidence_ref(page)
        _merge_entities(entities, knowledge.entities, page=page, ref=ref)
        _merge_assertions(assertions, knowledge.assertions, ref=ref, now=now)
        _merge_relations(relations, knowledge.relations, ref=ref)

    # Truncate entities FIRST, then drop edges whose endpoints did not survive.
    # Filtering before truncation left edges pointing at entities the cap had
    # since removed — a dangling foreign key at insert time, not a weak
    # relationship.
    kept_entities = dict(list(entities.items())[:MAX_ENTITIES_PER_CRAWL])
    kept_relations = {
        key: value
        for key, value in relations.items()
        if value.candidate.source in kept_entities
        and value.candidate.target in kept_entities
    }
    # Cap BEFORE grouping, for the same reason entities are truncated before
    # their edges: grouping the full set counted disputes between claims the cap
    # then discarded, and set ``disputed`` on rows that were never persisted. A
    # reader would see a contradiction total with no contradicting rows behind
    # it. Conflicts among the kept claims are the only ones this crawl can show.
    kept_assertions = dict(list(assertions.items())[:MAX_ASSERTIONS_PER_CRAWL])
    disputes = _group_contradictions(kept_assertions, vocabulary=vocabulary)

    return _Merged(
        entities=kept_entities,
        assertions=kept_assertions,
        relations=dict(list(kept_relations.items())[:MAX_RELATIONS_PER_CRAWL]),
        contradiction_count=disputes,
        warnings=tuple(dict.fromkeys(warnings)),
        pages_contributing=contributing,
    )


def _merge_entities(
    entities: dict[EntityRef, _MergedEntity],
    candidates: Sequence[EntityCandidate],
    *,
    page: _PageInput,
    ref: dict,
) -> None:
    for candidate in candidates:
        existing = entities.get(candidate.ref)
        if existing is None:
            entities[candidate.ref] = _MergedEntity(
                candidate=candidate, evidence=[ref], page_ids={page.site_url_id}
            )
            continue
        existing.page_ids.add(page.site_url_id)
        if len(existing.evidence) < MAX_EVIDENCE_REFS_PER_ROW:
            existing.evidence.append(ref)
        # A later page may carry the name an earlier one lacked; it may never
        # REPLACE one, so the first observed canonical name stands.
        if not existing.candidate.canonical_name and candidate.canonical_name:
            existing.candidate = candidate


def _merge_assertions(
    assertions: dict[tuple, _MergedAssertion],
    candidates: Sequence[AssertionCandidate],
    *,
    ref: dict,
    now: datetime,
) -> None:
    for candidate in candidates:
        resolved = resolve_temporal_state(
            page_temporal_state=candidate.temporal_state,
            effective_to=candidate.effective_to,
            now=now,
        )
        candidate = replace(candidate, temporal_state=resolved)
        key = (
            candidate.subject,
            candidate.predicate_id,
            candidate.scope_key,
            candidate.normalized_value,
        )
        existing = assertions.get(key)
        if existing is None:
            assertions[key] = _MergedAssertion(candidate=candidate, evidence=[ref])
            continue
        # The SAME claim on a second page is corroboration, not a new fact.
        if len(existing.evidence) < MAX_EVIDENCE_REFS_PER_ROW:
            existing.evidence.append(ref)


def _merge_relations(
    relations: dict[tuple, _MergedRelation],
    candidates: Sequence[RelationCandidate],
    *,
    ref: dict,
) -> None:
    for candidate in candidates:
        key = (candidate.relation_type_id, candidate.source, candidate.target)
        existing = relations.get(key)
        if existing is None:
            relations[key] = _MergedRelation(candidate=candidate, evidence=[ref])
        elif len(existing.evidence) < MAX_EVIDENCE_REFS_PER_ROW:
            existing.evidence.append(ref)


def _group_contradictions(
    assertions: dict[tuple, _MergedAssertion],
    *,
    vocabulary: KnowledgeVocabulary,
) -> int:
    """Flag every side of each disputed fact; return how many disputes exist.

    A contradiction is two or more DIFFERENT normalized values for the same
    subject, predicate, and scope, WHERE THE PACK SAYS ONLY ONE MAY HOLD.
    Multiplicity alone is not a conflict: a school publishes several phone
    numbers and several campuses, and the pack marks those predicates
    ``multiple_compatible`` / ``scoped_many`` for exactly that reason. Observed
    live on the first acceptance corpus, ignoring the policy reported six
    published phone numbers as a contradiction and flipped two questions to
    ``conflicting`` — a fabricated finding on a correct site.

    For a genuinely disputed fact nothing is deleted, reconciled, or ranked: all
    sides keep their evidence and stay ``observed`` so a reviewer decides.
    Historical values participate — a stale fee that still contradicts the
    current one is exactly what a reader needs to see — and no side is promoted
    to current truth by winning a count.

    The group's persisted UUID is derived at write time from the crawl plus the
    claim, so it is stable across replays without this pure step needing the
    crawl in scope.
    """

    by_claim: dict[tuple, list[_MergedAssertion]] = {}
    for merged in assertions.values():
        candidate = merged.candidate
        by_claim.setdefault(
            (candidate.subject, candidate.predicate_id, candidate.scope_key), []
        ).append(merged)

    disputes = 0
    for (_subject, predicate_id, _scope), members in by_claim.items():
        spec = vocabulary.predicates.get(predicate_id)
        if spec is not None and conflict_policy_permits_multiple(
            spec.conflict_policy, spec.cardinality
        ):
            continue
        # An UNSCOPED claim cannot contradict anything: two fee figures whose
        # academic year, grade, and fee type the site never stated may simply
        # be two different grades' fees, and calling that a conflict is a guess.
        # A fabricated conflict on a correct site is worse than a missed one —
        # it blocks publication of a fact that is fine. The real finding, that
        # the claim is unscoped, travels on the row via ``scope_complete``.
        #
        # Unscoped members are EXCLUDED rather than disqualifying the whole
        # claim: two fully-scoped values still contradict each other even when a
        # third, unscoped one sits beside them.
        scoped = [member for member in members if member.candidate.scope_complete]
        if len({member.candidate.normalized_value for member in scoped}) < 2:
            continue
        disputes += 1
        for member in scoped:
            member.disputed = True
    return disputes


def conflict_policy_permits_multiple(policy: str, cardinality: str) -> bool:
    """Whether a predicate legitimately holds several simultaneous values.

    Both signals are read. ``conflict_policy`` states how a clash is resolved;
    ``cardinality`` states whether several values may coexist at all. A
    predicate that is ``scoped_many`` or ``many`` is multi-valued by definition,
    whatever its policy says about resolving a genuine clash within one scope.
    An unrecognized policy falls through to "single value", the strict reading:
    a false contradiction is visible and reviewable, a missed one is not.
    """
    return (
        policy in NON_CONFLICTING_POLICIES or cardinality in MULTI_VALUE_CARDINALITIES
    )


# =========================================================================
# Persist
# =========================================================================
# PostgreSQL binds at most 32767 parameters per statement. A single
# ``VALUES`` batch spends one parameter per COLUMN per ROW, so the widest table
# here (assertions, 25 columns) exceeds the limit at roughly 1300 rows — well
# under ``MAX_ASSERTIONS_PER_CRAWL``. Chunking by parameter budget rather than a
# flat row count keeps every table under the ceiling as columns are added.
_MAX_BIND_PARAMS: Final = 30_000


async def _insert_chunked(
    session: AsyncSession,
    model: type,
    rows: list[dict],
    *,
    constraint: str,
) -> None:
    """Insert ``rows`` as conflict-safe batches within the bind-parameter cap.

    A large crawl reaches the configured caps legitimately; sending it as one
    statement fails the whole finalization with a driver-level parameter error
    after all the extraction work is already done.
    """
    if not rows:
        return
    per_row = max(1, len(rows[0]))
    size = max(1, _MAX_BIND_PARAMS // per_row)
    for start in range(0, len(rows), size):
        await session.execute(
            pg_insert(model)
            .values(rows[start : start + size])
            .on_conflict_do_nothing(constraint=constraint)
        )


async def _persist(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    manifest: Mapping[str, str],
    merged: _Merged,
) -> None:
    """Insert the crawl's knowledge rows, conflict-safe and idempotent.

    Deterministic IDs plus ``ON CONFLICT DO NOTHING`` make a re-run of
    finalization a no-op rather than a duplicate-key failure, which matters
    because finalization can legitimately be reached twice (worker
    terminalization and a cooperative cancel).
    """

    pack_id = str(manifest.get("pack_id", ""))[:64]
    pack_version = str(manifest.get("pack_version", ""))[:32]
    entity_rows = [
        {
            "id": entity_id(crawl.id, ref.entity_type_id, ref.identity_key),
            "workspace_id": crawl.workspace_id,
            "project_id": crawl.project_id,
            "crawl_id": crawl.id,
            "entity_type_id": ref.entity_type_id[:64],
            "identity_key": ref.identity_key[:256],
            "canonical_name": merged_entity.candidate.canonical_name[:512],
            "aliases": list(merged_entity.candidate.aliases),
            "identifiers": dict(merged_entity.candidate.identifiers),
            "review_state": REVIEW_STATE_OBSERVED,
            "evidence_refs": merged_entity.evidence,
            "evidence_page_count": len(merged_entity.page_ids),
            "industry_pack_id": pack_id,
            "industry_pack_version": pack_version,
            "extractor_version": KNOWLEDGE_EXTRACTOR_VERSION,
        }
        for ref, merged_entity in merged.entities.items()
    ]
    await _insert_chunked(
        session,
        KnowledgeEntity,
        entity_rows,
        constraint="uq_knowledge_entity_identity",
    )

    assertion_rows = []
    for merged_assertion in merged.assertions.values():
        candidate = merged_assertion.candidate
        subject_id = entity_id(
            crawl.id,
            candidate.subject.entity_type_id,
            candidate.subject.identity_key,
        )
        if candidate.subject not in merged.entities:
            # An assertion whose subject was never established has nothing to
            # be about. Dropping it is the only option that keeps the graph
            # honest; a synthesized subject would be a fabricated entity.
            continue
        group = (
            contradiction_group_id(
                crawl.id,
                subject_id,
                candidate.predicate_id,
                candidate.scope_key,
            )
            if merged_assertion.disputed
            else None
        )
        assertion_rows.append(
            {
                "id": assertion_id(
                    crawl.id,
                    subject_id,
                    candidate.predicate_id,
                    candidate.scope_key,
                    candidate.normalized_value,
                ),
                "workspace_id": crawl.workspace_id,
                "project_id": crawl.project_id,
                "crawl_id": crawl.id,
                "subject_entity_id": subject_id,
                "predicate_id": candidate.predicate_id[:64],
                "value_type": candidate.value_type[:16],
                "raw_value": candidate.raw_value[:512],
                "normalized_value": candidate.normalized_value[:512],
                "numeric_value": candidate.numeric_value,
                "unit": candidate.unit[:32],
                "currency": candidate.currency[:8],
                "scope": dict(candidate.scope),
                "scope_key": candidate.scope_key[:256],
                "effective_from": candidate.effective_from,
                "effective_to": candidate.effective_to,
                "temporal_state": candidate.temporal_state[:16],
                "scope_complete": candidate.scope_complete,
                "evidence_refs": merged_assertion.evidence,
                "derivation_method": candidate.derivation_method[:24],
                "extractor_version": KNOWLEDGE_EXTRACTOR_VERSION,
                "confidence": candidate.confidence,
                "review_state": REVIEW_STATE_OBSERVED,
                "contradiction_group_id": group,
                "industry_pack_id": pack_id,
                "industry_pack_version": pack_version,
            }
        )
    await _insert_chunked(
        session,
        KnowledgeAssertion,
        assertion_rows,
        constraint="uq_knowledge_assertion_claim",
    )

    relation_rows = [
        {
            "id": relation_id(
                crawl.id,
                merged_relation.candidate.relation_type_id,
                entity_id(
                    crawl.id,
                    merged_relation.candidate.source.entity_type_id,
                    merged_relation.candidate.source.identity_key,
                ),
                entity_id(
                    crawl.id,
                    merged_relation.candidate.target.entity_type_id,
                    merged_relation.candidate.target.identity_key,
                ),
            ),
            "workspace_id": crawl.workspace_id,
            "project_id": crawl.project_id,
            "crawl_id": crawl.id,
            "relation_type_id": merged_relation.candidate.relation_type_id[:64],
            "source_entity_id": entity_id(
                crawl.id,
                merged_relation.candidate.source.entity_type_id,
                merged_relation.candidate.source.identity_key,
            ),
            "target_entity_id": entity_id(
                crawl.id,
                merged_relation.candidate.target.entity_type_id,
                merged_relation.candidate.target.identity_key,
            ),
            "qualifiers": {},
            "temporal_state": merged_relation.candidate.temporal_state[:16],
            "evidence_refs": merged_relation.evidence,
            "derivation_method": merged_relation.candidate.derivation_method[:24],
            "extractor_version": KNOWLEDGE_EXTRACTOR_VERSION,
            "review_state": REVIEW_STATE_OBSERVED,
            "industry_pack_id": pack_id,
            "industry_pack_version": pack_version,
            "is_current": True,
        }
        for merged_relation in merged.relations.values()
    ]
    await _insert_chunked(
        session,
        KnowledgeRelation,
        relation_rows,
        constraint="uq_knowledge_relation_edge",
    )


def current_assertion(candidate: AssertionCandidate) -> bool:
    """Whether a claim may be presented as current truth."""
    return candidate.temporal_state == TEMPORAL_STATE_CURRENT


def historical_assertion(candidate: AssertionCandidate) -> bool:
    return candidate.temporal_state == TEMPORAL_STATE_HISTORICAL
