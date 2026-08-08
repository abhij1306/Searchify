# Deterministic Site Intelligence scoring: question coverage, journey coverage,
# and the six universal dimensions.
#
# PURE (no I/O, no ORM, no model call). Every input is a bounded value object
# the domain layer assembled from PERSISTED rows, so a read endpoint can render
# the result without recomputing anything, and the same crawl always scores the
# same way.
#
# The rule that shapes every number here: COMPOSITES REPORT OVER THE FULL
# DENOMINATOR, with coverage beside them. Renormalizing over only the dimensions
# a crawl could observe would rank a site with no schema graph, no policy pages,
# and no author attribution ABOVE a site that published all three and did them
# badly — it is missing exactly the dimensions it would have failed. Low
# coverage is itself the finding, and it is reported as one.
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.analysis.site_health.knowledge import (
    JourneySpec,
    KnowledgeVocabulary,
    QuestionSpec,
    StageSpec,
)
from app.core.config.site_health import TEMPORAL_STATE_HISTORICAL
from app.core.config.site_intelligence import (
    COMPONENT_LABELS,
    COVERAGE_ANSWERED_CREDIT,
    COVERAGE_ANSWERED_STRONG,
    COVERAGE_ANSWERED_WEAK,
    COVERAGE_CONFLICTING,
    COVERAGE_EXCLUDED_FROM_DENOMINATOR,
    COVERAGE_HISTORICAL_ONLY,
    COVERAGE_MISSING,
    COVERAGE_NOT_APPLICABLE,
    COVERAGE_STATES,
    COVERAGE_UNAVAILABLE_EVIDENCE,
    COVERAGE_UNSUPPORTED,
    DIMENSION_ANSWERABILITY,
    DIMENSION_COMPONENTS,
    DIMENSION_DISCOVERABILITY,
    DIMENSION_FORMULA_VERSION,
    DIMENSION_IDS,
    DIMENSION_JOURNEY,
    DIMENSION_KNOWLEDGE,
    DIMENSION_LABELS,
    DIMENSION_MACHINE,
    DIMENSION_TRUST,
    EXTRACTED_PREDICATE_SUFFIXES,
    JOURNEY_COVERAGE_VERSION,
    OUTCOME_STATE_UNAVAILABLE,
    QUESTION_COVERAGE_VERSION,
    VALUE_TYPE_MONEY,
)

__all__ = [
    "CorpusSignals",
    "DimensionReport",
    "JourneyReport",
    "KnowledgeIndex",
    "QuestionCoverage",
    "QuestionCoverageReport",
    "score_dimensions",
    "resolve_journeys",
    "resolve_question_coverage",
]


# =========================================================================
# Inputs
# =========================================================================
@dataclass(frozen=True)
class KnowledgeIndex:
    """The crawl's persisted knowledge, indexed for coverage resolution.

    Built once by the domain layer from ``knowledge_assertions`` /
    ``knowledge_entities`` rather than re-queried per question: the reason the
    three tables exist is that this lookup has to be cheap.
    """

    # predicate_id -> the temporal states of its current assertions
    predicate_states: Mapping[str, frozenset[str]] = field(default_factory=dict)
    # predicate_ids with at least one assertion inside a contradiction group
    disputed_predicates: frozenset[str] = frozenset()
    entity_type_ids: frozenset[str] = frozenset()
    entity_count: int = 0
    assertion_count: int = 0
    relation_count: int = 0
    contradiction_count: int = 0

    def has_any(self, predicate_id: str) -> bool:
        return bool(self.predicate_states.get(predicate_id))

    def has_usable(self, predicate_id: str) -> bool:
        """Whether a claim exists that may be presented as current.

        ``unknown`` counts, ``historical`` does not. Most pages carry no
        publication date at all, so their facts are extracted as ``unknown`` —
        that is the normal case on a live site, not a defect. Requiring an
        explicit ``current`` stamp reported every fact on every undated page as
        unanswered: measured on the first acceptance corpus, a school with its
        name, description, and nine contact points extracted still scored 0.0
        question coverage. What we must never do is let a fact we KNOW is stale
        answer a question, and ``historical`` is excluded for exactly that.
        """
        states = self.predicate_states.get(predicate_id) or frozenset()
        return bool(states - {TEMPORAL_STATE_HISTORICAL})

    def only_historical(self, predicate_id: str) -> bool:
        states = self.predicate_states.get(predicate_id) or frozenset()
        return bool(states) and states == frozenset({TEMPORAL_STATE_HISTORICAL})


@dataclass(frozen=True)
class CorpusSignals:
    """Crawl-wide observations the dimension formula reads.

    Every field is a COUNT or a ratio the domain layer read off persisted rows.
    A ``None`` ratio means the crawl could not observe the component at all —
    which is different from observing it and scoring zero, and is carried
    through to the report as ``unavailable``.
    """

    analyzed_pages: int = 0
    selected_pages: int = 0
    failed_pages: int = 0
    indexable_pages: int = 0
    canonical_ok_pages: int = 0
    linked_pages: int = 0
    pages_with_schema: int = 0
    pages_with_valid_schema: int = 0
    pages_with_schema_parity: int = 0
    pages_with_author: int = 0
    pages_with_dates: int = 0
    pages_with_outbound_citation: int = 0
    pages_with_question_headings: int = 0
    pages_with_answer_first: int = 0
    pages_with_usable_headings: int = 0
    policy_role_pages: int = 0
    conversion_action_pages: int = 0
    # role_id -> how many analyzed pages carry it (primary or secondary)
    role_page_counts: Mapping[str, int] = field(default_factory=dict)
    # role_id -> how many of those pages link onward to another role page
    role_continuity_counts: Mapping[str, int] = field(default_factory=dict)
    # How many roles the pack declares IN TOTAL — the denominator of
    # ``role_coverage``, whose numerator is the number of those roles some page
    # actually carries. Not the missing count: reading it that way inverts the
    # ratio and lets full coverage report as zero.
    declared_role_count: int = 0
    # Entity names observed in schema that disagree with the canonical entity.
    entity_name_conflicts: int = 0
    pages_with_entity_names: int = 0


# =========================================================================
# Question coverage
# =========================================================================
@dataclass(frozen=True)
class QuestionCoverage:
    question_id: str
    label: str
    state: str
    journey_stage_id: str
    # Named, renderable reason. A state without one is unexplainable to a user.
    reason: str
    satisfied_predicate_ids: tuple[str, ...] = ()
    missing_predicate_ids: tuple[str, ...] = ()
    answering_role_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestionCoverageReport:
    questions: tuple[QuestionCoverage, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    # Credit-weighted answered share over the full denominator (only
    # ``not_applicable`` leaves it). ``None`` when the pack declares no
    # applicable question at all — never 0.0, which would read as total failure.
    answered_ratio: float | None = None
    denominator: int = 0
    # question_id -> state, so journey resolution is a lookup rather than a scan
    # of all 29 questions for every stage of every journey.
    states: Mapping[str, str] = field(default_factory=dict)
    version: str = QUESTION_COVERAGE_VERSION

    def state_of(self, question_id: str) -> str:
        """A stage may require a question the pack never defined; that is a
        pack-authoring gap, and reporting it as ``missing`` keeps the stage's
        denominator honest instead of quietly shrinking it."""
        return self.states.get(question_id, COVERAGE_MISSING)


def resolve_question_coverage(
    *,
    vocabulary: KnowledgeVocabulary,
    knowledge: KnowledgeIndex,
    observed_role_ids: frozenset[str],
    acquisition_failed: bool,
    not_applicable_question_ids: frozenset[str] = frozenset(),
) -> QuestionCoverageReport:
    """Resolve every pack question to exactly one of the eight states.

    The states are ordered by what a reader must act on, and each is reached by
    a distinct condition — none of them is a fallback for another:

    ``not_applicable``      a reviewer declared it out of scope for this project
                            (the versioned project overlay). The only state
                            removed from the denominator.
    ``unavailable_evidence`` the pages that would answer it could not be
                            acquired. We did not look successfully; the site is
                            not being judged.
    ``conflicting``         a required fact is disputed. Reported ahead of any
                            "answered" state: a contradicted answer is worse
                            than a missing one, because it can be published.
    ``missing``             no page carries an applicable role and no required
                            fact exists. Looked, found nothing.
    ``unsupported``         an applicable-role page EXISTS but yields none of the
                            required facts. The site appears to address this and
                            does not actually answer it — the most commonly
                            actionable gap, and invisible if collapsed into
                            ``missing``.
    ``historical_only``     every required fact is historical while the question
                            needs a current one.
    ``answered_weak``       some required facts present, not all.
    ``answered_strong``     all required facts current, on a page whose role the
                            pack says should carry them.
    """

    coverages: list[QuestionCoverage] = []
    for question in vocabulary.questions:
        coverages.append(
            _resolve_one(
                question,
                knowledge=knowledge,
                observed_role_ids=observed_role_ids,
                acquisition_failed=acquisition_failed,
                not_applicable=question.question_id in not_applicable_question_ids,
                vocabulary=vocabulary,
            )
        )

    counts = dict.fromkeys(COVERAGE_STATES, 0)
    for coverage in coverages:
        counts[coverage.state] = counts.get(coverage.state, 0) + 1

    denominator = sum(
        1
        for coverage in coverages
        if coverage.state not in COVERAGE_EXCLUDED_FROM_DENOMINATOR
    )
    credit = sum(
        COVERAGE_ANSWERED_CREDIT.get(coverage.state, 0.0) for coverage in coverages
    )
    return QuestionCoverageReport(
        questions=tuple(coverages),
        counts=counts,
        answered_ratio=(round(credit / denominator, 4) if denominator else None),
        denominator=denominator,
        states={coverage.question_id: coverage.state for coverage in coverages},
    )


def _resolve_one(
    question: QuestionSpec,
    *,
    knowledge: KnowledgeIndex,
    observed_role_ids: frozenset[str],
    acquisition_failed: bool,
    not_applicable: bool,
    vocabulary: KnowledgeVocabulary,
) -> QuestionCoverage:
    answering = tuple(sorted(question.applicable_role_ids & observed_role_ids))
    required = tuple(question.required_predicate_ids)
    satisfied = tuple(p for p in required if knowledge.has_usable(p))
    present = tuple(p for p in required if knowledge.has_any(p))
    state, reason = _coverage_state(
        required=required,
        satisfied=satisfied,
        present=present,
        answering=answering,
        knowledge=knowledge,
        acquisition_failed=acquisition_failed,
        not_applicable=not_applicable,
        vocabulary=vocabulary,
    )
    return QuestionCoverage(
        question_id=question.question_id,
        label=question.label,
        state=state,
        journey_stage_id=question.journey_stage_id,
        reason=reason,
        satisfied_predicate_ids=satisfied,
        missing_predicate_ids=tuple(p for p in required if p not in present),
        answering_role_ids=answering,
    )


def _answered_state(
    required: Sequence[str],
    satisfied: Sequence[str],
    answering: Sequence[str],
) -> tuple[str, str] | None:
    """The ANSWERED_* state when every required fact is evidenced, else ``None``.

    A pack may declare a question with NO required predicates — one whose answer
    is that the page playing the role exists at all. That case needs its own
    arm: with nothing to satisfy, ``satisfied`` is empty and a truthiness test
    reads it as "nothing answered", so such a question used to fall through to
    ``unsupported`` and report a fully answered question as one this analyzer
    cannot extract. With no answering page it stays unanswered, as before.
    """
    if required:
        if len(satisfied) != len(required):
            return None
    elif not answering:
        return None
    if answering:
        return COVERAGE_ANSWERED_STRONG, "answered on the expected page"
    # The facts exist but not where the pack expects them. A reader looking for
    # this answer where it belongs will not find it.
    return COVERAGE_ANSWERED_WEAK, "facts present but not on a page for this role"


def _coverage_state(
    *,
    required: Sequence[str],
    satisfied: Sequence[str],
    present: Sequence[str],
    answering: Sequence[str],
    knowledge: KnowledgeIndex,
    acquisition_failed: bool,
    not_applicable: bool,
    vocabulary: KnowledgeVocabulary,
) -> tuple[str, str]:
    """The single state one question resolves to, in strict precedence order."""

    if not_applicable:
        return COVERAGE_NOT_APPLICABLE, "declared out of scope for the project"
    if any(p in knowledge.disputed_predicates for p in required):
        return COVERAGE_CONFLICTING, "a required fact has conflicting values"
    if not answering and acquisition_failed:
        return (
            COVERAGE_UNAVAILABLE_EVIDENCE,
            "no page that could answer this was successfully acquired",
        )
    answered = _answered_state(required, satisfied, answering)
    if answered is not None:
        return answered
    if present and all(knowledge.only_historical(p) for p in present):
        return COVERAGE_HISTORICAL_ONLY, "only historical evidence exists"
    if satisfied:
        return COVERAGE_ANSWERED_WEAK, "some required facts are present"
    if answering:
        return COVERAGE_UNSUPPORTED, _unsupported_reason(required, vocabulary)
    return COVERAGE_MISSING, "no page for this role and no supporting facts"


def is_extractable_predicate(
    predicate_id: str, vocabulary: KnowledgeVocabulary
) -> bool:
    """Whether this analyzer has ANY deterministic path to evidence a predicate.

    Two paths exist: the shared core suffixes, and any MONEY-typed predicate —
    money is bound through whatever predicate the active pack declares for it
    (``education.fee_amount``, ``commerce.price``) rather than a fixed name.
    Checking only the suffix list reported fees as "not machine extractable" on
    a page whose fee had just been extracted.
    """
    if predicate_id.split(".", 1)[-1] in EXTRACTED_PREDICATE_SUFFIXES:
        return True
    spec = vocabulary.predicates.get(predicate_id)
    return spec is not None and spec.value_type == VALUE_TYPE_MONEY


def _unsupported_reason(
    required_predicate_ids: Sequence[str], vocabulary: KnowledgeVocabulary
) -> str:
    """Why an existing page still does not answer its question.

    Distinguishes "the site does not state this" from "this slice's extractor
    cannot yet read it". Both leave the question unanswered, but only the first
    is a finding about the site, and reporting the second as one would send a
    user to fix a page that is already correct.
    """
    if required_predicate_ids and not any(
        is_extractable_predicate(predicate_id, vocabulary)
        for predicate_id in required_predicate_ids
    ):
        return "the required facts are not yet machine-extractable by this analyzer"
    return "the page exists but states none of the required facts"


# =========================================================================
# Journeys
# =========================================================================
@dataclass(frozen=True)
class StageReport:
    stage_id: str
    label: str
    order: int
    role_coverage: float
    question_coverage: float | None
    present_role_ids: tuple[str, ...]
    missing_role_ids: tuple[str, ...]
    answered_question_ids: tuple[str, ...]
    gap_question_ids: tuple[str, ...]
    # Every outcome is ``unavailable`` until Demand Intelligence supplies events.
    # It is NEVER numeric zero: "no conversions" and "no way to see conversions"
    # are opposite findings.
    outcomes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JourneyReport:
    journey_id: str
    label: str
    stages: tuple[StageReport, ...] = ()
    role_coverage: float = 0.0
    question_coverage: float | None = None
    version: str = JOURNEY_COVERAGE_VERSION


def resolve_journeys(
    *,
    vocabulary: KnowledgeVocabulary,
    observed_role_ids: frozenset[str],
    coverage: QuestionCoverageReport,
) -> tuple[JourneyReport, ...]:
    """Stage-by-stage coverage of each pack journey, over full denominators."""
    return tuple(
        _resolve_journey(journey, observed_role_ids, coverage)
        for journey in vocabulary.journeys
    )


def _resolve_journey(
    journey: JourneySpec,
    observed_role_ids: frozenset[str],
    coverage: QuestionCoverageReport,
) -> JourneyReport:
    stages = tuple(
        _resolve_stage(stage, observed_role_ids, coverage) for stage in journey.stages
    )
    return JourneyReport(
        journey_id=journey.journey_id,
        label=journey.label,
        stages=stages,
        role_coverage=_mean([stage.role_coverage for stage in stages]) or 0.0,
        question_coverage=_mean([stage.question_coverage for stage in stages]),
    )


def _resolve_stage(
    stage: StageSpec,
    observed_role_ids: frozenset[str],
    coverage: QuestionCoverageReport,
) -> StageReport:
    present = tuple(r for r in stage.required_role_ids if r in observed_role_ids)
    answered = tuple(
        q
        for q in stage.required_question_ids
        if coverage.state_of(q) in (COVERAGE_ANSWERED_STRONG, COVERAGE_ANSWERED_WEAK)
    )
    return StageReport(
        stage_id=stage.stage_id,
        label=stage.label,
        order=stage.order,
        role_coverage=_ratio(len(present), len(stage.required_role_ids)) or 0.0,
        question_coverage=_ratio(len(answered), len(stage.required_question_ids)),
        present_role_ids=present,
        missing_role_ids=tuple(
            r for r in stage.required_role_ids if r not in observed_role_ids
        ),
        answered_question_ids=answered,
        gap_question_ids=tuple(
            q for q in stage.required_question_ids if q not in answered
        ),
        # Every outcome is ``unavailable`` until Demand Intelligence supplies
        # events — never numeric zero.
        outcomes=dict.fromkeys(stage.outcome_ids, OUTCOME_STATE_UNAVAILABLE),
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    """A ratio, or ``None`` when there is nothing to measure.

    ``None`` and ``0.0`` are kept apart everywhere in this module: a stage that
    requires no questions has no score, and giving it 0.0 would drag a journey's
    mean down for a requirement the pack never made.
    """
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _mean(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


# =========================================================================
# Dimensions
# =========================================================================
@dataclass(frozen=True)
class ComponentScore:
    component_id: str
    label: str
    # ``None`` == unavailable: the crawl could not observe this at all.
    score: float | None


@dataclass(frozen=True)
class DimensionScore:
    dimension_id: str
    label: str
    # Over the FULL component denominator; an unavailable component scores 0.
    score: float
    # What share of the components were observable at all.
    coverage: float
    components: tuple[ComponentScore, ...] = ()


@dataclass(frozen=True)
class DimensionReport:
    dimensions: tuple[DimensionScore, ...] = ()
    # Mean over all six dimensions, always. Never renormalized.
    composite_score: float = 0.0
    composite_coverage: float = 0.0
    version: str = DIMENSION_FORMULA_VERSION


def score_dimensions(
    *,
    signals: CorpusSignals,
    knowledge: KnowledgeIndex,
    coverage: QuestionCoverageReport,
    journeys: Sequence[JourneyReport],
    vocabulary: KnowledgeVocabulary,
) -> DimensionReport:
    """Score all six dimensions from persisted evidence. PURE."""

    raw = {
        DIMENSION_DISCOVERABILITY: _discoverability(signals),
        DIMENSION_KNOWLEDGE: _knowledge(signals, knowledge, vocabulary),
        DIMENSION_ANSWERABILITY: _answerability(signals, coverage),
        DIMENSION_TRUST: _trust(signals, knowledge),
        DIMENSION_JOURNEY: _journey(signals, journeys),
        DIMENSION_MACHINE: _machine(signals),
    }

    dimensions: list[DimensionScore] = []
    for dimension_id in DIMENSION_IDS:
        component_ids = DIMENSION_COMPONENTS[dimension_id]
        values = raw.get(dimension_id, {})
        components = tuple(
            ComponentScore(
                component_id=component_id,
                label=COMPONENT_LABELS.get(component_id, component_id),
                score=values.get(component_id),
            )
            for component_id in component_ids
        )
        observed = [c for c in components if c.score is not None]
        total = len(components) or 1
        dimensions.append(
            DimensionScore(
                dimension_id=dimension_id,
                label=DIMENSION_LABELS[dimension_id],
                # FULL denominator: an unavailable component contributes 0.
                score=round(sum(c.score or 0.0 for c in observed) / total, 4),
                coverage=round(len(observed) / total, 4),
                components=components,
            )
        )

    return DimensionReport(
        dimensions=tuple(dimensions),
        # Full six-dimension denominator, always.
        composite_score=round(sum(d.score for d in dimensions) / len(DIMENSION_IDS), 4),
        composite_coverage=round(
            sum(d.coverage for d in dimensions) / len(DIMENSION_IDS), 4
        ),
    )


def _share(numerator: int, denominator: int) -> float | None:
    return _ratio(numerator, denominator)


def _discoverability(signals: CorpusSignals) -> dict[str, float | None]:
    analyzed = signals.analyzed_pages
    attempted = signals.analyzed_pages + signals.failed_pages
    return {
        "indexable_ratio": _share(signals.indexable_pages, analyzed),
        "canonical_integrity": _share(signals.canonical_ok_pages, analyzed),
        "internal_reachability": _share(signals.linked_pages, analyzed),
        "acquisition_success": _share(signals.analyzed_pages, attempted),
    }


def _knowledge(
    signals: CorpusSignals,
    knowledge: KnowledgeIndex,
    vocabulary: KnowledgeVocabulary,
) -> dict[str, float | None]:
    """Knowledge completeness.

    ``predicate_coverage`` measures only the predicates this analyzer can
    actually evidence. Scoring it against every predicate the pack declares
    would report a permanent ceiling that no site could ever reach, which is a
    statement about the analyzer disguised as a finding about the customer.
    """
    extractable = [
        predicate_id
        for predicate_id in vocabulary.predicates
        if is_extractable_predicate(predicate_id, vocabulary)
    ]
    asserted = sum(1 for predicate_id in extractable if knowledge.has_any(predicate_id))
    declared_entity_types = len(vocabulary.entity_types) or 1
    return {
        "identity_entity": 1.0 if knowledge.entity_count else 0.0,
        "offering_entities": _share(
            len(knowledge.entity_type_ids), declared_entity_types
        ),
        # Unavailable, always: no deterministic signal identifies an audience
        # today. Scoring it 0.0 would blame a site for a gap in this analyzer,
        # which is the one thing the coverage rule exists to prevent.
        "audience_entities": None,
        "predicate_coverage": _share(asserted, len(extractable)),
        "relation_coverage": (
            None if not knowledge.entity_count else _bounded(knowledge.relation_count)
        ),
        "role_coverage": _share(
            len(signals.role_page_counts), signals.declared_role_count
        ),
    }


def _bounded(count: int) -> float:
    """A presence signal from a count: any is better than none, capped at 1."""
    return 1.0 if count > 0 else 0.0


def _answerability(
    signals: CorpusSignals, coverage: QuestionCoverageReport
) -> dict[str, float | None]:
    analyzed = signals.analyzed_pages
    return {
        "question_coverage": coverage.answered_ratio,
        "question_units": _share(signals.pages_with_question_headings, analyzed),
        "answer_first": _share(signals.pages_with_answer_first, analyzed),
        "heading_structure": _share(signals.pages_with_usable_headings, analyzed),
    }


def _trust(
    signals: CorpusSignals, knowledge: KnowledgeIndex
) -> dict[str, float | None]:
    analyzed = signals.analyzed_pages
    return {
        "authorship": _share(signals.pages_with_author, analyzed),
        "dated_content": _share(signals.pages_with_dates, analyzed),
        "policy_evidence": _bounded(signals.policy_role_pages),
        "external_citation": _share(signals.pages_with_outbound_citation, analyzed),
        # Freedom from unresolved conflict. Unmeasurable with no assertions at
        # all — a site with no facts is not thereby free of contradictions.
        "contradiction_freedom": (
            None
            if not knowledge.assertion_count
            else round(
                max(
                    0.0,
                    1.0 - knowledge.contradiction_count / knowledge.assertion_count,
                ),
                4,
            )
        ),
    }


def _journey(
    signals: CorpusSignals, journeys: Sequence[JourneyReport]
) -> dict[str, float | None]:
    if not journeys:
        return dict.fromkeys(DIMENSION_COMPONENTS[DIMENSION_JOURNEY])
    role_coverage = _mean([journey.role_coverage for journey in journeys])
    question_coverage = _mean([journey.question_coverage for journey in journeys])
    role_pages = sum(signals.role_page_counts.values())
    return {
        "stage_role_coverage": role_coverage,
        "stage_question_coverage": question_coverage,
        "stage_conversion_actions": _share(
            signals.conversion_action_pages, signals.analyzed_pages
        ),
        "stage_continuity": _share(
            sum(signals.role_continuity_counts.values()), role_pages
        ),
    }


def _machine(signals: CorpusSignals) -> dict[str, float | None]:
    analyzed = signals.analyzed_pages
    with_schema = signals.pages_with_schema
    return {
        "schema_presence": _share(with_schema, analyzed),
        # Validity and parity are only measurable where schema exists. On a site
        # with none they are ``unavailable``, not 0.0 — and ``schema_presence``
        # already scores 0, so the absence is counted once rather than three
        # times over.
        "schema_validity": _share(signals.pages_with_valid_schema, with_schema),
        "schema_visible_parity": _share(signals.pages_with_schema_parity, with_schema),
        "entity_consistency": (
            None
            if not signals.pages_with_entity_names
            else round(
                max(
                    0.0,
                    1.0
                    - signals.entity_name_conflicts / signals.pages_with_entity_names,
                ),
                4,
            )
        ),
    }
