# Deterministic typed-knowledge extraction (Site Intelligence S2).
#
# PURE: no I/O, no ORM, no model call. Turns one page's bounded, already-
# persisted facts plus the crawl's frozen pack vocabulary into candidate
# entities, assertions, and relations. The same facts under the same pack always
# yield the same candidates, which is what makes the S2 gate ("identical
# artifacts reproduce identical working knowledge") checkable.
#
# Two rules govern every extractor below:
#
#   1. EVIDENCE IS MANDATORY. A candidate that cannot name the artifact it came
#      from is not produced. A missing fact becomes a coverage gap, never a
#      guessed assertion — an invented fee or deadline is the single most
#      damaging output this system could produce.
#   2. THE VISIBLE PATH IS PRIMARY. The first acceptance corpus publishes zero
#      structured data. An extractor that could only read JSON-LD would report
#      an empty knowledge model for a real school and call it a finding, so
#      every entity category is reachable from visible evidence and schema is
#      treated as one corroborating signal among several.
#
# Extraction targets the twelve predicate suffixes and the entity categories
# shared by all sixteen catalog packs, so Education and Commerce run the same
# code path with different vocabularies bound in.
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from app.core.config.site_health import (
    TEMPORAL_STATE_CURRENT,
    TEMPORAL_STATE_HISTORICAL,
    TEMPORAL_STATE_UNKNOWN,
)
from app.core.config.site_intelligence import (
    CATEGORY_COMMERCIAL,
    CATEGORY_DOCUMENT,
    CATEGORY_EVENT,
    CATEGORY_OFFERING,
    CATEGORY_ORGANIZATION,
    CATEGORY_PERSON,
    CATEGORY_PLACE,
    CATEGORY_POLICY,
    DERIVATION_STRUCTURED_DATA,
    DERIVATION_URL_STRUCTURE,
    DERIVATION_VISIBLE_TEXT,
    KNOWLEDGE_EXTRACTOR_VERSION,
    MAX_ASSERTIONS_PER_PAGE,
    MAX_CANONICAL_NAME_CHARS,
    MAX_ENTITY_ALIASES,
    MAX_IDENTITY_KEY_CHARS,
    MAX_VALUE_CHARS,
    PREDICATE_ADDRESS,
    PREDICATE_CONTACT_POINT,
    PREDICATE_DESCRIPTION,
    PREDICATE_EFFECTIVE_DATE,
    PREDICATE_LEGAL_NAME,
    PREDICATE_POLICY_SUMMARY,
    SCHEMA_TYPE_CATEGORIES,
    TITLE_SEPARATORS,
    VALUE_TYPE_DATE,
    VALUE_TYPE_MONEY,
    VALUE_TYPE_OBJECT,
    VALUE_TYPE_STRING,
)

__all__ = [
    "AssertionCandidate",
    "EntityCandidate",
    "EntityRef",
    "KnowledgeVocabulary",
    "PageKnowledge",
    "RelationCandidate",
    "compile_vocabulary",
    "extract_page_knowledge",
    "scope_key_for",
]


# =========================================================================
# Compiled pack vocabulary
# =========================================================================
@dataclass(frozen=True)
class PredicateSpec:
    predicate_id: str
    suffix: str
    value_type: str
    cardinality: str
    conflict_policy: str
    required_scope: tuple[str, ...]
    subject_entity_type_ids: frozenset[str]
    temporal: bool


@dataclass(frozen=True)
class EntityTypeSpec:
    entity_type_id: str
    category: str
    label: str
    identity_fields: tuple[str, ...]


@dataclass(frozen=True)
class RelationSpec:
    relation_type_id: str
    source_entity_type_ids: frozenset[str]
    target_entity_type_ids: frozenset[str]


@dataclass(frozen=True)
class RoleSpec:
    role_id: str
    entity_type_ids: tuple[str, ...]
    required_question_ids: tuple[str, ...]
    temporal_policy: str


@dataclass(frozen=True)
class QuestionSpec:
    question_id: str
    label: str
    journey_stage_id: str
    applicable_role_ids: frozenset[str]
    required_predicate_ids: tuple[str, ...]
    required_entity_type_ids: tuple[str, ...]
    temporal_requirement: str
    intent: str


@dataclass(frozen=True)
class StageSpec:
    stage_id: str
    label: str
    order: int
    required_role_ids: tuple[str, ...]
    required_question_ids: tuple[str, ...]
    outcome_ids: tuple[str, ...]


@dataclass(frozen=True)
class JourneySpec:
    journey_id: str
    label: str
    stages: tuple[StageSpec, ...]
    outcomes: Mapping[str, str]


@dataclass(frozen=True)
class KnowledgeVocabulary:
    """One pack's knowledge vocabulary, compiled once per process.

    Immutable and hashable-by-identity: the domain layer caches one of these per
    (pack, version, content hash) so the per-page loop performs no file I/O and
    no dict rebuilding.
    """

    pack_id: str
    pack_version: str
    entity_types: Mapping[str, EntityTypeSpec]
    category_types: Mapping[str, tuple[str, ...]]
    predicates: Mapping[str, PredicateSpec]
    predicate_by_suffix: Mapping[str, PredicateSpec]
    relations: tuple[RelationSpec, ...]
    roles: Mapping[str, RoleSpec]
    questions: tuple[QuestionSpec, ...]
    journeys: tuple[JourneySpec, ...]
    # Predicates the pack declared without any subject entity type. The catalog
    # schema does not require the field, so such a predicate would silently
    # reject every subject and produce nothing. Surfaced as a named build
    # warning instead: a pack-authoring defect must be visible, not read as a
    # customer site with no facts.
    unusable_predicate_ids: tuple[str, ...] = ()

    def primary_type_for(self, category: str) -> str:
        """The pack's canonical entity type for a shared category, or ``""``.

        Several types can share a category (``education.location`` and
        ``education.campus`` are both ``place``). The FIRST declared wins and the
        choice is stable, because it is the pack file's own ordering — the same
        crawl re-run must not bind the same evidence to a different type.
        """
        types = self.category_types.get(category) or ()
        return types[0] if types else ""


def _tuple_of_str(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _mappings(pack: Mapping, key: str, id_field: str) -> list[Mapping]:
    """The pack's well-formed entries under ``key``, each carrying an id.

    A malformed entry is skipped rather than raising: the catalog validator is
    the place that rejects a bad pack, and a compile that died here would take
    an entire crawl down over one unusable definition.
    """
    return [
        raw
        for raw in pack.get(key) or ()
        if isinstance(raw, Mapping) and str(raw.get(id_field) or "")
    ]


def _compile_entity_types(
    pack: Mapping,
) -> tuple[dict[str, EntityTypeSpec], dict[str, tuple[str, ...]]]:
    entity_types: dict[str, EntityTypeSpec] = {}
    category_types: dict[str, list[str]] = {}
    for raw in _mappings(pack, "entity_types", "entity_type_id"):
        spec = EntityTypeSpec(
            entity_type_id=str(raw["entity_type_id"]),
            category=str(raw.get("category") or ""),
            label=str(raw.get("label") or ""),
            identity_fields=_tuple_of_str(raw.get("identity_fields")),
        )
        entity_types[spec.entity_type_id] = spec
        category_types.setdefault(spec.category, []).append(spec.entity_type_id)
    return entity_types, {key: tuple(ids) for key, ids in category_types.items()}


def _compile_predicates(
    pack: Mapping,
) -> tuple[dict[str, PredicateSpec], dict[str, PredicateSpec], tuple[str, ...]]:
    predicates: dict[str, PredicateSpec] = {}
    by_suffix: dict[str, PredicateSpec] = {}
    unusable: list[str] = []
    for raw in _mappings(pack, "assertion_predicates", "predicate_id"):
        predicate_id = str(raw["predicate_id"])
        spec = PredicateSpec(
            predicate_id=predicate_id,
            suffix=predicate_id.split(".", 1)[-1],
            value_type=str(raw.get("value_type") or VALUE_TYPE_STRING),
            cardinality=str(raw.get("cardinality") or "one"),
            conflict_policy=str(raw.get("conflict_policy") or "single_current"),
            required_scope=_tuple_of_str(raw.get("required_scope")),
            subject_entity_type_ids=frozenset(
                _tuple_of_str(raw.get("subject_entity_type_ids"))
            ),
            temporal=bool(raw.get("temporal")),
        )
        predicates[predicate_id] = spec
        if not spec.subject_entity_type_ids:
            unusable.append(predicate_id)
        # First declaration wins, so a pack that adds a private predicate whose
        # suffix collides with a core one cannot capture the core meaning.
        by_suffix.setdefault(spec.suffix, spec)
    return predicates, by_suffix, tuple(unusable)


def _compile_journey(raw: Mapping) -> JourneySpec:
    return JourneySpec(
        journey_id=str(raw["journey_id"]),
        label=str(raw.get("label") or ""),
        stages=tuple(
            StageSpec(
                stage_id=str(stage.get("stage_id") or ""),
                label=str(stage.get("label") or ""),
                order=int(stage.get("order") or 0),
                required_role_ids=_tuple_of_str(stage.get("required_role_ids")),
                required_question_ids=_tuple_of_str(stage.get("required_question_ids")),
                outcome_ids=_tuple_of_str(stage.get("outcome_ids")),
            )
            for stage in raw.get("stages") or ()
            if isinstance(stage, Mapping)
        ),
        outcomes={
            str(outcome["outcome_id"]): str(outcome.get("label") or "")
            for outcome in raw.get("outcomes") or ()
            if isinstance(outcome, Mapping) and outcome.get("outcome_id")
        },
    )


def compile_vocabulary(pack: Mapping) -> KnowledgeVocabulary:
    """Compile one pack mapping into the extractor's vocabulary. PURE."""

    entity_types, category_types = _compile_entity_types(pack)
    predicates, predicate_by_suffix, unusable_predicates = _compile_predicates(pack)
    return KnowledgeVocabulary(
        pack_id=str(pack.get("pack_id") or ""),
        pack_version=str(pack.get("version") or ""),
        entity_types=entity_types,
        category_types=category_types,
        predicates=predicates,
        predicate_by_suffix=predicate_by_suffix,
        relations=tuple(
            RelationSpec(
                relation_type_id=str(raw["relation_type_id"]),
                source_entity_type_ids=frozenset(
                    _tuple_of_str(raw.get("source_entity_type_ids"))
                ),
                target_entity_type_ids=frozenset(
                    _tuple_of_str(raw.get("target_entity_type_ids"))
                ),
            )
            for raw in _mappings(pack, "relation_types", "relation_type_id")
        ),
        roles={
            str(raw["role_id"]): RoleSpec(
                role_id=str(raw["role_id"]),
                entity_type_ids=_tuple_of_str(raw.get("entity_type_ids")),
                required_question_ids=_tuple_of_str(raw.get("required_question_ids")),
                temporal_policy=str(raw.get("temporal_policy") or ""),
            )
            for raw in _mappings(pack, "page_roles", "role_id")
        },
        questions=tuple(
            QuestionSpec(
                question_id=str(raw["question_id"]),
                label=str(raw.get("label") or ""),
                journey_stage_id=str(raw.get("journey_stage_id") or ""),
                applicable_role_ids=frozenset(
                    _tuple_of_str(raw.get("applicable_role_ids"))
                ),
                required_predicate_ids=_tuple_of_str(raw.get("required_predicate_ids")),
                required_entity_type_ids=_tuple_of_str(
                    raw.get("required_entity_type_ids")
                ),
                temporal_requirement=str(raw.get("temporal_requirement") or ""),
                intent=str(raw.get("intent") or ""),
            )
            for raw in _mappings(pack, "question_contracts", "question_id")
        ),
        journeys=tuple(
            _compile_journey(raw) for raw in _mappings(pack, "journeys", "journey_id")
        ),
        unusable_predicate_ids=unusable_predicates,
    )


# =========================================================================
# Candidates
# =========================================================================
@dataclass(frozen=True)
class EntityRef:
    """The cross-page identity of one entity: type plus normalized key."""

    entity_type_id: str
    identity_key: str


@dataclass(frozen=True)
class EntityCandidate:
    ref: EntityRef
    canonical_name: str
    aliases: tuple[str, ...] = ()
    identifiers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AssertionCandidate:
    subject: EntityRef
    predicate_id: str
    value_type: str
    raw_value: str
    normalized_value: str
    scope: Mapping[str, str]
    scope_key: str
    derivation_method: str
    numeric_value: float | None = None
    unit: str = ""
    currency: str = ""
    temporal_state: str = TEMPORAL_STATE_UNKNOWN
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    confidence: float = 1.0
    # Whether every qualifier the pack REQUIRES for this predicate was actually
    # evidenced. False means the claim is real but we do not know what it
    # applies to — a fee whose academic year, grade, and fee type the page never
    # stated. Two such claims are NOT a contradiction: they may simply be two
    # different grades' fees, and reporting a conflict would be a guess. The
    # honest finding is that neither is scoped.
    scope_complete: bool = True


@dataclass(frozen=True)
class RelationCandidate:
    relation_type_id: str
    source: EntityRef
    target: EntityRef
    derivation_method: str
    temporal_state: str = TEMPORAL_STATE_UNKNOWN


@dataclass(frozen=True)
class PageKnowledge:
    """Everything one page contributes, before cross-page merging."""

    entities: tuple[EntityCandidate, ...] = ()
    assertions: tuple[AssertionCandidate, ...] = ()
    relations: tuple[RelationCandidate, ...] = ()
    # Named reasons a fact could not be produced. These become report copy, not
    # log noise: "no organization name on the crawl root" is a finding.
    warnings: tuple[str, ...] = ()
    extractor_version: str = KNOWLEDGE_EXTRACTOR_VERSION


# =========================================================================
# Normalization
# =========================================================================
_WHITESPACE = re.compile(r"\s+")
_NON_KEY_CHARS = re.compile(r"[^a-z0-9]+")


def normalize_text(value: object, *, limit: int = MAX_VALUE_CHARS) -> str:
    """NFKC-folded, whitespace-collapsed, bounded text."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return _WHITESPACE.sub(" ", text).strip()[:limit]


def identity_key_for(*parts: object) -> str:
    """The deterministic cross-page identity key for a set of identity fields.

    Case-folded and punctuation-stripped so "Riverside Academy", "RIVERSIDE
    ACADEMY" and "Riverside Academy." are one entity. Empty parts are kept as
    empty segments rather than dropped, so ``(name, domain)`` and
    ``(domain, name)`` can never collide.
    """
    segments = [
        _NON_KEY_CHARS.sub("-", normalize_text(part).casefold()).strip("-")
        for part in parts
    ]
    return "|".join(segments)[:MAX_IDENTITY_KEY_CHARS]


def scope_key_for(scope: Mapping[str, str]) -> str:
    """Deterministic serialization of an assertion's qualifiers.

    Sorted by key so two extractions of the same scope always produce the same
    string — the value is part of the assertion's identity AND of its
    contradiction group, so an unstable serialization would split one disputed
    fact into two undisputed ones.
    """
    return ";".join(
        f"{key}={normalize_text(scope[key], limit=64).casefold()}"
        for key in sorted(scope)
        if str(scope.get(key) or "").strip()
    )[:MAX_IDENTITY_KEY_CHARS]


def _strip_title_suffix(title: str) -> str:
    """The most specific segment of a page title.

    A title is commonly ``"<page> | <organization>"`` or the reverse. The
    LONGEST segment is the wrong choice (it is usually the page's own topic);
    the organization name is taken from the crawl root, where the title is
    normally the organization alone or ``"<organization> | <tagline>"``, so the
    FIRST segment is correct there and is what this returns.
    """
    text = normalize_text(title, limit=MAX_CANONICAL_NAME_CHARS)
    for separator in TITLE_SEPARATORS:
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if head:
                return head
    return text


def _parse_date(value: object) -> datetime | None:
    """Parse an ISO-8601 date/datetime, or ``None``. Never guesses a format."""
    text = normalize_text(value, limit=64)
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    for parser in (datetime.fromisoformat, date.fromisoformat):
        try:
            parsed = parser(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, datetime):
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)
    return None


# =========================================================================
# Extraction
# =========================================================================
def extract_page_knowledge(
    facts: Mapping,
    *,
    vocabulary: KnowledgeVocabulary,
    industry_role_id: str | None,
    temporal_state: str,
    site_identity_key: str,
    is_crawl_root: bool,
    final_url: str,
) -> PageKnowledge:
    """Everything one analyzed page contributes to the knowledge model.

    ``site_identity_key`` is the crawl's stable organization key, computed once
    by the caller from the root page: every page's assertions attach to the SAME
    organization entity, which is what makes "the address on /contact" and "the
    name on /about" facts about one subject rather than two.

    An unpacked crawl (``vocabulary`` with no entity types) yields nothing —
    the "classifier never ran" state, not an empty-business finding.
    """

    entities: list[EntityCandidate] = []
    assertions: list[AssertionCandidate] = []
    relations: list[RelationCandidate] = []
    warnings: list[str] = []

    org_type = vocabulary.primary_type_for(CATEGORY_ORGANIZATION)
    if not org_type or not site_identity_key:
        return PageKnowledge(warnings=("no_organization_entity_type",))
    org_ref = EntityRef(entity_type_id=org_type, identity_key=site_identity_key)

    blocks = _recognized_blocks(facts)
    role = vocabulary.roles.get(industry_role_id or "")

    _extract_organization(
        facts,
        blocks=blocks,
        org_ref=org_ref,
        is_crawl_root=is_crawl_root,
        vocabulary=vocabulary,
        temporal_state=temporal_state,
        entities=entities,
        assertions=assertions,
        warnings=warnings,
    )
    _extract_schema_entities(
        blocks=blocks,
        vocabulary=vocabulary,
        org_ref=org_ref,
        temporal_state=temporal_state,
        entities=entities,
        assertions=assertions,
        relations=relations,
    )
    _extract_role_entity(
        facts,
        role=role,
        vocabulary=vocabulary,
        org_ref=org_ref,
        temporal_state=temporal_state,
        final_url=final_url,
        is_crawl_root=is_crawl_root,
        entities=entities,
        assertions=assertions,
        relations=relations,
    )
    _extract_contact_points(
        facts,
        vocabulary=vocabulary,
        org_ref=org_ref,
        temporal_state=temporal_state,
        assertions=assertions,
    )
    _extract_money(
        facts,
        role=role,
        vocabulary=vocabulary,
        entities=entities,
        relations=relations,
        temporal_state=temporal_state,
        final_url=final_url,
        assertions=assertions,
        warnings=warnings,
    )
    _extract_dates(
        facts,
        vocabulary=vocabulary,
        org_ref=org_ref,
        entities=entities,
        temporal_state=temporal_state,
        assertions=assertions,
    )

    return PageKnowledge(
        entities=tuple(_dedupe_entities(entities)),
        assertions=tuple(assertions[:MAX_ASSERTIONS_PER_PAGE]),
        relations=tuple(dict.fromkeys(relations)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _recognized_blocks(facts: Mapping) -> tuple[Mapping, ...]:
    structured = facts.get("structured_data") or {}
    return tuple(
        block
        for block in (structured.get("blocks") or ())
        if isinstance(block, Mapping)
    )


def _dedupe_entities(candidates: Sequence[EntityCandidate]) -> list[EntityCandidate]:
    """Merge same-ref candidates, unioning aliases and identifiers."""
    merged: dict[EntityRef, EntityCandidate] = {}
    for candidate in candidates:
        existing = merged.get(candidate.ref)
        if existing is None:
            merged[candidate.ref] = candidate
            continue
        aliases = tuple(dict.fromkeys((*existing.aliases, *candidate.aliases)))[
            :MAX_ENTITY_ALIASES
        ]
        merged[candidate.ref] = EntityCandidate(
            ref=candidate.ref,
            canonical_name=existing.canonical_name or candidate.canonical_name,
            aliases=aliases,
            identifiers={**dict(existing.identifiers), **dict(candidate.identifiers)},
        )
    return list(merged.values())


def _add_assertion(
    assertions: list[AssertionCandidate],
    *,
    vocabulary: KnowledgeVocabulary,
    suffix: str,
    subject: EntityRef,
    raw_value: object,
    normalized: str,
    derivation: str,
    scope: Mapping[str, str] | None = None,
    numeric_value: float | None = None,
    currency: str = "",
    temporal_state: str = TEMPORAL_STATE_UNKNOWN,
    effective_from: datetime | None = None,
    value_type: str | None = None,
    confidence: float = 1.0,
) -> None:
    """Append one assertion if the active pack declares the predicate.

    A pack that does not declare the predicate simply produces no assertion:
    inventing a predicate id the pack never defined would give a value nothing
    could later interpret, score, or contradict.
    """
    spec = vocabulary.predicate_by_suffix.get(suffix)
    if spec is None or not normalized:
        return
    if subject.entity_type_id not in spec.subject_entity_type_ids:
        return
    resolved_scope = dict(scope or {})
    assertions.append(
        AssertionCandidate(
            scope_complete=_scope_is_complete(spec, resolved_scope),
            subject=subject,
            predicate_id=spec.predicate_id,
            value_type=value_type or spec.value_type,
            raw_value=normalize_text(raw_value),
            normalized_value=normalized[:MAX_VALUE_CHARS],
            scope=resolved_scope,
            scope_key=scope_key_for(resolved_scope),
            derivation_method=derivation,
            numeric_value=numeric_value,
            currency=currency,
            temporal_state=temporal_state,
            effective_from=effective_from,
            confidence=confidence,
        )
    )


def _organization_schema_evidence(
    blocks: Sequence[Mapping],
) -> tuple[list[str], list[str]]:
    """Organization names and ``sameAs`` profiles declared in structured data."""
    names: list[str] = []
    same_as: list[str] = []
    for block in blocks:
        category = SCHEMA_TYPE_CATEGORIES.get(str(block.get("type") or ""))
        if category != CATEGORY_ORGANIZATION:
            continue
        name = normalize_text(block.get("name"), limit=MAX_CANONICAL_NAME_CHARS)
        if name:
            names.append(name)
        same_as.extend(
            normalize_text(entry, limit=256) for entry in block.get("same_as") or ()
        )
    return names, [entry for entry in same_as if entry]


def _scope_is_complete(spec: PredicateSpec, scope: Mapping[str, str]) -> bool:
    """Whether every pack-required qualifier was evidenced, not defaulted."""
    return all(str(scope.get(key) or "").strip() for key in spec.required_scope)


def _extract_organization(
    facts: Mapping,
    *,
    blocks: Sequence[Mapping],
    org_ref: EntityRef,
    is_crawl_root: bool,
    vocabulary: KnowledgeVocabulary,
    temporal_state: str,
    entities: list[EntityCandidate],
    assertions: list[AssertionCandidate],
    warnings: list[str],
) -> None:
    """The one organization entity, and its name/description assertions.

    Named from structured data when present, otherwise from the crawl ROOT's
    title. Only the root may name the organization: taking the name from any
    page's title would rename the school on every section page.
    """
    schema_names, same_as = _organization_schema_evidence(blocks)
    title_name = _strip_title_suffix(facts.get("title") or "") if is_crawl_root else ""
    canonical = schema_names[0] if schema_names else title_name

    if not canonical and is_crawl_root:
        warnings.append("organization_name_absent_from_root")

    if canonical or schema_names:
        entities.append(
            EntityCandidate(
                ref=org_ref,
                canonical_name=canonical,
                # Empty entries are dropped BEFORE deduplication: ``title_name``
                # is "" on every non-root page, and an empty alias would be
                # persisted as a real observed spelling.
                aliases=tuple(
                    dict.fromkeys(name for name in (*schema_names, title_name) if name)
                )[:MAX_ENTITY_ALIASES],
                identifiers=(
                    {"same_as": ",".join(dict.fromkeys(same_as))[:MAX_VALUE_CHARS]}
                    if same_as
                    else {}
                ),
            )
        )
        _add_assertion(
            assertions,
            vocabulary=vocabulary,
            suffix=PREDICATE_LEGAL_NAME,
            subject=org_ref,
            raw_value=canonical,
            normalized=normalize_text(canonical).casefold(),
            derivation=(
                DERIVATION_STRUCTURED_DATA if schema_names else DERIVATION_VISIBLE_TEXT
            ),
            temporal_state=temporal_state,
            # A name read from a <title> is a strong but weaker signal than one
            # a site declared in its own structured data.
            confidence=1.0 if schema_names else 0.8,
        )

    if is_crawl_root:
        description = normalize_text(facts.get("meta_description"))
        _add_assertion(
            assertions,
            vocabulary=vocabulary,
            suffix=PREDICATE_DESCRIPTION,
            subject=org_ref,
            raw_value=description,
            normalized=description.casefold(),
            derivation=DERIVATION_VISIBLE_TEXT,
            temporal_state=temporal_state,
        )


def _extract_schema_entities(
    *,
    blocks: Sequence[Mapping],
    vocabulary: KnowledgeVocabulary,
    org_ref: EntityRef,
    temporal_state: str,
    entities: list[EntityCandidate],
    assertions: list[AssertionCandidate],
    relations: list[RelationCandidate],
) -> None:
    """Entities a page declared in structured data (person, place, event, offer)."""
    for block in blocks:
        category = SCHEMA_TYPE_CATEGORIES.get(str(block.get("type") or ""))
        if category in (None, CATEGORY_ORGANIZATION):
            continue
        name = normalize_text(block.get("name"), limit=MAX_CANONICAL_NAME_CHARS)
        if not name:
            continue
        entity_type_id = vocabulary.primary_type_for(str(category))
        if not entity_type_id:
            continue
        # Scoped by the organization key so two sites' "Main Campus" never merge.
        ref = EntityRef(
            entity_type_id=entity_type_id,
            identity_key=identity_key_for(name, org_ref.identity_key),
        )
        entities.append(EntityCandidate(ref=ref, canonical_name=name))
        _link_entities(
            vocabulary=vocabulary,
            source=ref,
            target=org_ref,
            temporal_state=temporal_state,
            relations=relations,
        )
        if category == CATEGORY_PLACE:
            _add_assertion(
                assertions,
                vocabulary=vocabulary,
                suffix=PREDICATE_ADDRESS,
                subject=org_ref,
                raw_value=name,
                normalized=normalize_text(name).casefold(),
                derivation=DERIVATION_STRUCTURED_DATA,
                scope={"location": name},
                value_type=VALUE_TYPE_OBJECT,
                temporal_state=temporal_state,
            )


def _extract_role_entity(
    facts: Mapping,
    *,
    role: RoleSpec | None,
    vocabulary: KnowledgeVocabulary,
    org_ref: EntityRef,
    temporal_state: str,
    final_url: str,
    is_crawl_root: bool,
    entities: list[EntityCandidate],
    assertions: list[AssertionCandidate],
    relations: list[RelationCandidate],
) -> None:
    """The entity a role page IS, identified by the page and named by its H1.

    This is the path that works on a site with no structured data: a page the
    pack classified as ``program_detail`` and headed "Cambridge IGCSE" *is* that
    offering, and a ``fees`` page *is* a fee schedule. Only categories where the
    page-is-the-thing relationship genuinely holds are extracted.

    IDENTITY is the page path, not the H1. Most pack types identify on fields a
    page cannot supply — a fee schedule identifies on ``(organization,
    academic_year, scope)`` and an admission window on ``(organization,
    academic_year, grade_scope)`` — so keying on the heading would assert a name
    the type does not have AND collapse two academic years into one entity. The
    page path is the identity these things actually have on this site: stable
    across a redesign that rewrites every heading, and distinct per page.

    The crawl ROOT is excluded. Its H1 is the organization's own name, and the
    ``institution_home`` role legitimately declares a campus type — so without
    this the homepage mints a campus named after the school, and every later
    address or fee on the site attaches to that phantom place.
    """
    if role is None or is_crawl_root:
        return
    headings = facts.get("headings") or {}
    h1_texts = headings.get("h1_texts") or ()
    name = normalize_text(
        h1_texts[0] if h1_texts else "", limit=MAX_CANONICAL_NAME_CHARS
    )
    if not name:
        return
    # ONLY the role's first declared type. Packs list a role's primary subject
    # first and the rest as types the page may MENTION, so falling through to a
    # later one invents an entity of a type the page has nothing to do with.
    # Observed live: five pages classified ``institution_home`` (whose primary
    # type is the organization, already established) fell through to its second
    # type and became five campuses named "Our History", "Vision & Mission",
    # "Category Press Release"... and an FAQ page became a program. A page whose
    # primary subject IS the organization contributes nothing new here.
    primary_type_id = role.entity_type_ids[0] if role.entity_type_ids else ""
    spec = vocabulary.entity_types.get(primary_type_id)
    if spec is None or spec.category not in _PAGE_IS_ENTITY_CATEGORIES:
        return
    ref = EntityRef(
        entity_type_id=primary_type_id,
        identity_key=identity_key_for(_path_scope(final_url), org_ref.identity_key),
    )
    entities.append(
        EntityCandidate(
            ref=ref,
            canonical_name=name,
            aliases=(name,),
            identifiers={"page_url": final_url[:MAX_VALUE_CHARS]},
        )
    )
    _link_entities(
        vocabulary=vocabulary,
        source=ref,
        target=org_ref,
        temporal_state=temporal_state,
        relations=relations,
    )
    if spec.category == CATEGORY_POLICY:
        summary = normalize_text(facts.get("first_answer_text"))
        _add_assertion(
            assertions,
            vocabulary=vocabulary,
            suffix=PREDICATE_POLICY_SUMMARY,
            subject=ref,
            raw_value=summary,
            normalized=summary.casefold(),
            derivation=DERIVATION_VISIBLE_TEXT,
            temporal_state=temporal_state,
        )


# Categories where "this page IS this thing" holds. Deliberately narrow: an
# admissions-overview page describes a process, it is not an entity named
# "Admissions".
_PAGE_IS_ENTITY_CATEGORIES = frozenset(
    {
        CATEGORY_COMMERCIAL,
        CATEGORY_OFFERING,
        CATEGORY_PERSON,
        CATEGORY_PLACE,
        CATEGORY_EVENT,
        CATEGORY_POLICY,
        CATEGORY_DOCUMENT,
    }
)


def _link_entities(
    *,
    vocabulary: KnowledgeVocabulary,
    source: EntityRef,
    target: EntityRef,
    temporal_state: str,
    relations: list[RelationCandidate],
) -> None:
    """Connect two entities via the pack's own relation type, or not at all.

    The relation TYPE is never invented: the first pack relation whose declared
    source types include the source and whose target types include the target is
    used. When the pack declares no such edge, none is created — an invented
    relation type is a claim about the business the pack never authorized.
    """
    if source == target:
        return
    for spec in vocabulary.relations:
        if (
            source.entity_type_id in spec.source_entity_type_ids
            and target.entity_type_id in spec.target_entity_type_ids
        ):
            relations.append(
                RelationCandidate(
                    relation_type_id=spec.relation_type_id,
                    source=source,
                    target=target,
                    derivation_method=DERIVATION_URL_STRUCTURE,
                    temporal_state=temporal_state,
                )
            )
            return


def _extract_contact_points(
    facts: Mapping,
    *,
    vocabulary: KnowledgeVocabulary,
    org_ref: EntityRef,
    temporal_state: str,
    assertions: list[AssertionCandidate],
) -> None:
    """Declared contact points, scoped by channel as the pack requires."""
    for point in facts.get("contact_points") or ():
        if not isinstance(point, Mapping):
            continue
        channel = normalize_text(point.get("channel"), limit=32).casefold()
        value = normalize_text(point.get("value"), limit=256)
        if not channel or not value:
            continue
        _add_assertion(
            assertions,
            vocabulary=vocabulary,
            suffix=PREDICATE_CONTACT_POINT,
            subject=org_ref,
            raw_value=value,
            normalized=value.casefold(),
            derivation=DERIVATION_VISIBLE_TEXT,
            # ``purpose`` is REQUIRED scope the page cannot evidence, so it is
            # recorded as ``general`` rather than guessed from surrounding copy.
            scope={"channel": channel, "purpose": "general"},
            value_type=VALUE_TYPE_OBJECT,
            temporal_state=temporal_state,
        )


def _usable_amount(value: object) -> float | None:
    """A real numeric amount, or ``None``.

    ``bool`` is a subclass of ``int``, so a JSON ``true`` arriving in a
    persisted money fact passed a bare ``isinstance(value, (int, float))`` and
    published itself as a fee of 1.00.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _own_money_subject(
    role: RoleSpec,
    entities: Sequence[EntityCandidate],
    money_predicates: Sequence[PredicateSpec],
) -> tuple[PredicateSpec, EntityRef] | None:
    """A money predicate whose subject is an entity type this ROLE itself owns.

    Restricted to the role's own types on purpose. Scanning every extracted
    entity let a merely-mentioned one — a schema-declared place or person that
    happens to be a valid money subject — win and short-circuit the guarded
    resolution that keeps a page's price attached to the page's own subject.
    """
    own_types = set(role.entity_type_ids)
    for candidate in entities:
        if candidate.ref.entity_type_id not in own_types:
            continue
        for spec in money_predicates:
            if candidate.ref.entity_type_id in spec.subject_entity_type_ids:
                return spec, candidate.ref
    return None


def _money_binding(
    role: RoleSpec | None,
    vocabulary: KnowledgeVocabulary,
    entities: list[EntityCandidate],
    relations: list[RelationCandidate],
    *,
    temporal_state: str,
) -> tuple[PredicateSpec, EntityRef] | None:
    """The predicate and subject a money amount on this page belongs to.

    Resolved from the pack's own declarations rather than a named predicate,
    which is what lets one extractor serve every pack. The two shapes packs
    actually use are both handled:

    - the page's own entity carries the money (an Education fees page IS a fee
      schedule, and ``fee_amount`` is asserted about a fee schedule);
    - the money belongs to a SEPARATE commercial entity the role also declares
      (a Commerce PDP is a product, and ``price`` is asserted about the offer,
      matching how schema.org separates a product from its offer).

    In the second case the offer is minted here, sharing the page's identity key
    — the product and its offer are two facets of one page, not two pages — and
    linked to the page's primary entity through whatever relation the pack
    declares between them.

    ``None`` is the honest outcome for a page with a number on it and nothing
    the pack says that number could be about.
    """
    if role is None:
        return None
    money_predicates = [
        spec
        for spec in vocabulary.predicates.values()
        if spec.value_type == VALUE_TYPE_MONEY
    ]
    if not money_predicates:
        return None
    own = _own_money_subject(role, entities, money_predicates)
    if own is not None:
        return own
    primary = next(
        (
            candidate
            for candidate in entities
            if candidate.ref.entity_type_id == (role.entity_type_ids or ("",))[0]
        ),
        None,
    )
    # Only the page's OWN entity may carry its money. ``entities[0]`` is the
    # organization on a root page and a schema-declared place or person
    # elsewhere, so anchoring on it would attach a price to whatever the page
    # happened to mention first.
    if primary is None:
        return None
    for entity_type_id in role.entity_type_ids:
        for spec in money_predicates:
            if entity_type_id not in spec.subject_entity_type_ids:
                continue
            ref = EntityRef(
                entity_type_id=entity_type_id,
                identity_key=primary.ref.identity_key,
            )
            entities.append(
                EntityCandidate(ref=ref, canonical_name=primary.canonical_name)
            )
            _link_entities(
                vocabulary=vocabulary,
                source=ref,
                target=primary.ref,
                temporal_state=temporal_state,
                relations=relations,
            )
            return spec, ref
    return None


def _extract_money(
    facts: Mapping,
    *,
    role: RoleSpec | None,
    vocabulary: KnowledgeVocabulary,
    entities: list[EntityCandidate],
    relations: list[RelationCandidate],
    temporal_state: str,
    final_url: str,
    assertions: list[AssertionCandidate],
    warnings: list[str],
) -> None:
    """Currency-qualified amounts, bound to what the pack says they describe.

    Requiring a resolvable subject is what stops a phone extension, a student
    count, or a donation banner from becoming a published fee: a number is only
    money if this page also established something the pack allows money to be
    asserted about. A page with amounts and no such subject records a warning
    instead of an assertion, so the omission is visible rather than silent.
    """
    mentions = [m for m in facts.get("money_mentions") or () if isinstance(m, Mapping)]
    if not mentions:
        return
    binding = _money_binding(
        role, vocabulary, entities, relations, temporal_state=temporal_state
    )
    if binding is None:
        warnings.append("money_mentions_without_a_pack_declared_subject")
        return
    spec, subject = binding
    for mention in mentions:
        currency = normalize_text(mention.get("currency"), limit=8).upper()
        amount = _usable_amount(mention.get("amount"))
        if not currency or amount is None:
            continue
        assertions.append(
            AssertionCandidate(
                subject=subject,
                predicate_id=spec.predicate_id,
                value_type=VALUE_TYPE_MONEY,
                raw_value=normalize_text(mention.get("raw")),
                normalized_value=f"{currency} {float(amount):.2f}",
                # Every other required qualifier (academic year, grade, fee
                # type, timing) is left OUT rather than filled with a plausible
                # default. An amount whose period we invented is exactly the
                # fabricated fact this system must never publish, and its
                # absence is what tells a reviewer the claim is unscoped.
                scope={"currency": currency, "offering": _path_scope(final_url)},
                scope_key=scope_key_for(
                    {"currency": currency, "offering": _path_scope(final_url)}
                ),
                scope_complete=_scope_is_complete(
                    spec, {"currency": currency, "offering": _path_scope(final_url)}
                ),
                derivation_method=DERIVATION_VISIBLE_TEXT,
                numeric_value=float(amount),
                currency=currency,
                temporal_state=temporal_state,
                # Visible copy near an amount is suggestive, not authoritative:
                # the page states the number but rarely states what it covers.
                confidence=0.7,
            )
        )


def _path_scope(final_url: str) -> str:
    """The URL path as the offering scope — the only scope the page evidences."""
    try:
        return (urlsplit(final_url).path or "/")[:128]
    except ValueError:
        return ""


def _extract_dates(
    facts: Mapping,
    *,
    vocabulary: KnowledgeVocabulary,
    org_ref: EntityRef,
    entities: Sequence[EntityCandidate],
    temporal_state: str,
    assertions: list[AssertionCandidate],
) -> None:
    """The page's declared effective date, attached to what the page is about.

    A modified date is deliberately NOT used: a template change touches every
    page's ``dateModified`` without changing a single fact, and treating that as
    an effective date would make stale content look freshly verified.
    """
    dates = facts.get("dates") or {}
    published = _parse_date(dates.get("published"))
    if published is None:
        return
    spec = vocabulary.predicate_by_suffix.get(PREDICATE_EFFECTIVE_DATE)
    allowed = spec.subject_entity_type_ids if spec else frozenset()
    subject = next(
        (c.ref for c in entities if c.ref.entity_type_id in allowed),
        org_ref,
    )
    _add_assertion(
        assertions,
        vocabulary=vocabulary,
        suffix=PREDICATE_EFFECTIVE_DATE,
        subject=subject,
        raw_value=dates.get("published"),
        normalized=published.date().isoformat(),
        derivation=DERIVATION_VISIBLE_TEXT,
        value_type=VALUE_TYPE_DATE,
        effective_from=published,
        temporal_state=temporal_state,
    )


def resolve_temporal_state(
    *, page_temporal_state: str, effective_to: datetime | None, now: datetime
) -> str:
    """The temporal state a claim is published in.

    An expired effective period makes a claim historical whatever the page says,
    because the page is what is out of date. Without dates the PAGE's state is
    the answer, and ``unknown`` stays ``unknown`` — never promoted to
    ``current`` for convenience.
    """
    if effective_to is not None and effective_to < now:
        return TEMPORAL_STATE_HISTORICAL
    if page_temporal_state in (TEMPORAL_STATE_HISTORICAL, TEMPORAL_STATE_CURRENT):
        return page_temporal_state
    return TEMPORAL_STATE_UNKNOWN
