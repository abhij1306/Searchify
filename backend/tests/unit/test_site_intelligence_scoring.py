"""Question coverage, journey coverage, and the full-denominator dimension rule.

The properties under test are the ones a wrong answer would quietly corrupt: the
eight coverage states must stay distinguishable, and no composite may improve
because evidence was missing.
"""

from __future__ import annotations

import pytest

from app.analysis.site_health.intelligence import (
    CorpusSignals,
    KnowledgeIndex,
    resolve_journeys,
    resolve_question_coverage,
    score_dimensions,
)
from app.analysis.site_health.knowledge import compile_vocabulary
from app.core.config.industry_packs.catalog import load_pack
from app.core.config.site_intelligence import (
    COVERAGE_ANSWERED_STRONG,
    COVERAGE_ANSWERED_WEAK,
    COVERAGE_CONFLICTING,
    COVERAGE_HISTORICAL_ONLY,
    COVERAGE_MISSING,
    COVERAGE_STATES,
    COVERAGE_UNAVAILABLE_EVIDENCE,
    COVERAGE_UNSUPPORTED,
    DIMENSION_IDS,
    OUTCOME_STATE_UNAVAILABLE,
)

FEES_QUESTION = "education.fees"
FEES_ROLE = "education.fees"


@pytest.fixture(scope="module")
def education():
    return compile_vocabulary(load_pack("education", "1.0.0"))


@pytest.fixture(scope="module")
def fees_question(education):
    return next(q for q in education.questions if q.question_id == FEES_QUESTION)


def index(
    *,
    current: tuple[str, ...] = (),
    historical: tuple[str, ...] = (),
    disputed: tuple[str, ...] = (),
    **kwargs,
) -> KnowledgeIndex:
    states: dict[str, set[str]] = {}
    for predicate_id in current:
        states.setdefault(predicate_id, set()).add("current")
    for predicate_id in historical:
        states.setdefault(predicate_id, set()).add("historical")
    return KnowledgeIndex(
        predicate_states={key: frozenset(value) for key, value in states.items()},
        disputed_predicates=frozenset(disputed),
        **kwargs,
    )


def state_of(education, question_id: str, **kwargs) -> str:
    report = resolve_question_coverage(
        vocabulary=education,
        knowledge=kwargs.pop("knowledge", index()),
        observed_role_ids=kwargs.pop("observed_role_ids", frozenset()),
        acquisition_failed=kwargs.pop("acquisition_failed", False),
        **kwargs,
    )
    return report.state_of(question_id)


# =========================================================================
# The eight states stay distinct
# =========================================================================
def test_all_eight_states_are_reachable(education, fees_question):
    required = fees_question.required_predicate_ids
    roles = frozenset({FEES_ROLE})
    observed = {
        state_of(education, FEES_QUESTION),
        state_of(education, FEES_QUESTION, observed_role_ids=roles),
        state_of(education, FEES_QUESTION, acquisition_failed=True),
        state_of(
            education,
            FEES_QUESTION,
            knowledge=index(current=required),
            observed_role_ids=roles,
        ),
        state_of(education, FEES_QUESTION, knowledge=index(current=required[:1])),
        state_of(education, FEES_QUESTION, knowledge=index(historical=required)),
        state_of(education, FEES_QUESTION, knowledge=index(disputed=required)),
        state_of(
            education,
            FEES_QUESTION,
            not_applicable_question_ids=frozenset({FEES_QUESTION}),
        ),
    }
    assert observed == set(COVERAGE_STATES)


def test_missing_means_no_page_and_no_facts(education):
    assert state_of(education, FEES_QUESTION) == COVERAGE_MISSING


def test_unsupported_means_the_page_exists_but_states_nothing(education):
    """The most actionable gap, and invisible if collapsed into ``missing``."""
    assert (
        state_of(education, FEES_QUESTION, observed_role_ids=frozenset({FEES_ROLE}))
        == COVERAGE_UNSUPPORTED
    )


def test_unavailable_evidence_means_we_could_not_look(education):
    """Distinct from ``missing``: the site is not being judged here."""
    assert (
        state_of(education, FEES_QUESTION, acquisition_failed=True)
        == COVERAGE_UNAVAILABLE_EVIDENCE
    )


def test_a_disputed_fact_outranks_every_answered_state(education, fees_question):
    """A contradicted answer is worse than a missing one: it can be published."""
    required = fees_question.required_predicate_ids
    assert (
        state_of(
            education,
            FEES_QUESTION,
            knowledge=index(current=required, disputed=required),
            observed_role_ids=frozenset({FEES_ROLE}),
        )
        == COVERAGE_CONFLICTING
    )


def test_historical_evidence_never_answers_a_current_question(education, fees_question):
    assert (
        state_of(
            education,
            FEES_QUESTION,
            knowledge=index(historical=fees_question.required_predicate_ids),
            observed_role_ids=frozenset({FEES_ROLE}),
        )
        == COVERAGE_HISTORICAL_ONLY
    )


def test_facts_on_the_wrong_page_are_only_a_weak_answer(education, fees_question):
    """A reader looking where the answer belongs will not find it."""
    assert (
        state_of(
            education,
            FEES_QUESTION,
            knowledge=index(current=fees_question.required_predicate_ids),
        )
        == COVERAGE_ANSWERED_WEAK
    )


def test_answered_strong_needs_both_the_page_and_the_facts(education, fees_question):
    assert (
        state_of(
            education,
            FEES_QUESTION,
            knowledge=index(current=fees_question.required_predicate_ids),
            observed_role_ids=frozenset({FEES_ROLE}),
        )
        == COVERAGE_ANSWERED_STRONG
    )


def test_unsupported_names_an_unextractable_requirement_as_such(education):
    """A gap in the ANALYZER must not be reported as a gap in the site."""
    report = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset({"education.results_outcomes"}),
        acquisition_failed=False,
    )
    results = next(
        question
        for question in report.questions
        if question.question_id == "education.results"
    )
    assert results.state == COVERAGE_UNSUPPORTED
    assert "machine-extractable" in results.reason


# =========================================================================
# Denominators
# =========================================================================
def test_not_applicable_is_the_only_state_that_leaves_the_denominator(education):
    full = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset(),
        acquisition_failed=False,
    )
    excused = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset(),
        acquisition_failed=False,
        not_applicable_question_ids=frozenset({FEES_QUESTION}),
    )
    assert full.denominator == len(education.questions)
    assert excused.denominator == full.denominator - 1


def test_unavailable_evidence_stays_in_the_denominator(education):
    """Missing evidence is the finding, not an excuse to shrink the measure."""
    report = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset(),
        acquisition_failed=True,
    )
    assert report.counts[COVERAGE_UNAVAILABLE_EVIDENCE] > 0
    assert report.denominator == len(education.questions)


def test_every_question_excused_reports_no_ratio_rather_than_zero(education):
    """An empty denominator has no ratio — and zero would be an accusation.

    ``0.0`` is the score of a site that answered nothing. A project whose
    reviewer excused every question answered nothing because there was nothing
    to answer, and reporting those two identically turns a scoping decision into
    a failing grade.
    """
    report = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset(),
        acquisition_failed=False,
        not_applicable_question_ids=frozenset(
            question.question_id for question in education.questions
        ),
    )
    assert report.denominator == 0
    assert report.answered_ratio is None


def test_a_site_answering_nothing_scores_zero_not_none(education):
    report = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset(),
        acquisition_failed=False,
    )
    assert report.answered_ratio == 0.0


def test_every_question_resolves_to_exactly_one_known_state(education):
    report = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset({FEES_ROLE}),
        acquisition_failed=False,
    )
    assert len(report.questions) == len(education.questions)
    assert all(question.state in COVERAGE_STATES for question in report.questions)
    assert sum(report.counts.values()) == len(education.questions)


def test_every_state_carries_a_renderable_reason(education):
    report = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset({FEES_ROLE}),
        acquisition_failed=False,
    )
    assert all(question.reason for question in report.questions)


# =========================================================================
# Journeys
# =========================================================================
def test_journey_outcomes_are_unavailable_never_zero(education):
    """No conversions and no way to see conversions are opposite findings."""
    coverage = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset(),
        acquisition_failed=False,
    )
    journeys = resolve_journeys(
        vocabulary=education, observed_role_ids=frozenset(), coverage=coverage
    )
    outcomes = [
        state
        for journey in journeys
        for stage in journey.stages
        for state in stage.outcomes.values()
    ]
    assert outcomes
    assert set(outcomes) == {OUTCOME_STATE_UNAVAILABLE}


def test_journey_stage_reports_the_roles_it_is_missing(education):
    coverage = resolve_question_coverage(
        vocabulary=education,
        knowledge=index(),
        observed_role_ids=frozenset({"education.fees"}),
        acquisition_failed=False,
    )
    journeys = resolve_journeys(
        vocabulary=education,
        observed_role_ids=frozenset({"education.fees"}),
        coverage=coverage,
    )
    evaluate = next(
        stage for stage in journeys[0].stages if stage.stage_id == "education.evaluate"
    )
    assert "education.fees" in evaluate.present_role_ids
    assert "education.curriculum" in evaluate.missing_role_ids
    assert 0.0 < evaluate.role_coverage < 1.0


# =========================================================================
# Dimensions — the rule that must never bend
# =========================================================================
def _report(education, signals: CorpusSignals, knowledge: KnowledgeIndex):
    coverage = resolve_question_coverage(
        vocabulary=education,
        knowledge=knowledge,
        observed_role_ids=frozenset(),
        acquisition_failed=False,
    )
    journeys = resolve_journeys(
        vocabulary=education, observed_role_ids=frozenset(), coverage=coverage
    )
    return score_dimensions(
        signals=signals,
        knowledge=knowledge,
        coverage=coverage,
        journeys=journeys,
        vocabulary=education,
    )


def test_composite_always_divides_by_all_six_dimensions(education):
    report = _report(education, CorpusSignals(), index())
    assert len(report.dimensions) == len(DIMENSION_IDS)
    assert report.composite_score == round(
        sum(d.score for d in report.dimensions) / len(DIMENSION_IDS), 4
    )


def test_publishing_nothing_never_scores_better_than_publishing_it_badly(education):
    """The rule this whole formula exists to enforce.

    A site with no schema graph is missing exactly the dimension it would have
    failed. If unavailable components were dropped from the denominator, it
    would outscore a site that published schema and got it wrong.
    """
    published_badly = CorpusSignals(
        analyzed_pages=10,
        pages_with_schema=10,
        pages_with_valid_schema=0,
        pages_with_schema_parity=0,
    )
    published_nothing = CorpusSignals(analyzed_pages=10, pages_with_schema=0)

    bad = _report(education, published_badly, index())
    absent = _report(education, published_nothing, index())

    def machine(report):
        return next(d for d in report.dimensions if d.dimension_id == "machine_clarity")

    assert machine(absent).score <= machine(bad).score
    assert machine(absent).coverage < machine(bad).coverage


def test_coverage_reports_how_much_was_observable(education):
    blind = _report(education, CorpusSignals(), index())
    seeing = _report(
        education,
        CorpusSignals(
            analyzed_pages=10,
            indexable_pages=10,
            canonical_ok_pages=10,
            linked_pages=8,
            pages_with_schema=5,
            pages_with_valid_schema=5,
            pages_with_schema_parity=5,
            declared_role_count=19,
            role_page_counts={"education.fees": 1},
        ),
        index(entity_count=3, assertion_count=9),
    )
    assert seeing.composite_coverage > blind.composite_coverage


def test_an_unmeasurable_component_is_unavailable_not_zero(education):
    """Schema validity on a site with no schema is unknown, not failed.

    Counting it as 0.0 would penalize the same absence three times over —
    presence, validity, and parity — for one missing thing.
    """
    report = _report(education, CorpusSignals(analyzed_pages=5), index())
    machine = next(d for d in report.dimensions if d.dimension_id == "machine_clarity")

    def component(name):
        return next(c for c in machine.components if c.component_id == name)

    validity = component("schema_validity")
    presence = component("schema_presence")

    assert validity.score is None
    assert presence.score == 0.0


def test_contradiction_freedom_is_unmeasurable_without_assertions(education):
    """A site with no facts is not thereby free of contradictions."""
    report = _report(education, CorpusSignals(analyzed_pages=5), index())
    trust = next(d for d in report.dimensions if d.dimension_id == "trust_evidence")
    freedom = next(
        c for c in trust.components if c.component_id == "contradiction_freedom"
    )
    assert freedom.score is None


def test_contradictions_reduce_trust_when_facts_exist(education):
    clean = _report(
        education,
        CorpusSignals(analyzed_pages=5),
        index(assertion_count=10, contradiction_count=0),
    )
    disputed = _report(
        education,
        CorpusSignals(analyzed_pages=5),
        index(assertion_count=10, contradiction_count=5),
    )

    def trust(report):
        return next(d for d in report.dimensions if d.dimension_id == "trust_evidence")

    assert trust(disputed).score < trust(clean).score
