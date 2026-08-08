"""Deterministic knowledge extraction against the real Education/Commerce packs.

Uses the shipped catalog rather than a hand-written fixture pack: the extractor's
whole design claim is that it reads what the packs actually declare, and a
bespoke fixture would let a vocabulary mismatch pass unnoticed.
"""

from __future__ import annotations

import pytest

from app.analysis.site_health.knowledge import (
    compile_vocabulary,
    extract_page_knowledge,
    identity_key_for,
    normalize_text,
    scope_key_for,
)
from app.analysis.site_health.parser import extract_page_facts
from app.core.config.industry_packs.catalog import load_pack

SITE_KEY = "example-test"


@pytest.fixture(scope="module")
def education():
    return compile_vocabulary(load_pack("education", "1.0.0"))


@pytest.fixture(scope="module")
def commerce():
    return compile_vocabulary(load_pack("commerce", "1.0.0"))


def knowledge(vocabulary, html: str, *, role: str | None, url: str, root: bool = False):
    facts = extract_page_facts(html.encode("utf-8"), final_url=url, charset="utf-8")
    return extract_page_knowledge(
        facts,
        vocabulary=vocabulary,
        industry_role_id=role,
        temporal_state="current",
        site_identity_key=SITE_KEY,
        is_crawl_root=root,
        final_url=url,
    )


def predicates(result) -> set[str]:
    return {assertion.predicate_id for assertion in result.assertions}


def entity_types(result) -> set[str]:
    return {entity.ref.entity_type_id for entity in result.entities}


# =========================================================================
# Vocabulary compilation
# =========================================================================
def test_education_vocabulary_compiles_from_the_shipped_pack(education):
    assert education.pack_id == "education"
    assert education.primary_type_for("organization") == "education.organization"
    # The exact catalog count belongs to the pack-contract tests; what this
    # compile must guarantee is that questions arrive fully formed.
    assert education.questions
    assert all(q.question_id and q.required_predicate_ids for q in education.questions)
    assert len(education.journeys) == 1
    assert education.journeys[0].stages[0].order == 0


def test_malformed_pack_entries_are_skipped_not_fatal():
    """One unusable definition must not take a whole crawl down."""
    vocabulary = compile_vocabulary(
        {
            "pack_id": "p",
            "version": "1",
            "entity_types": [
                {"entity_type_id": "p.org", "category": "organization"},
                {"category": "organization"},  # no id
                "not-a-mapping",
            ],
        }
    )
    assert set(vocabulary.entity_types) == {"p.org"}


# =========================================================================
# Identity
# =========================================================================
def test_identity_key_folds_case_and_punctuation():
    assert identity_key_for("Riverside Academy") == identity_key_for(
        "RIVERSIDE  ACADEMY."
    )


def test_identity_key_keeps_empty_segments_so_field_order_cannot_collide():
    assert identity_key_for("a", "") != identity_key_for("", "a")


def test_scope_key_is_order_independent():
    assert scope_key_for({"currency": "INR", "grade": "8"}) == scope_key_for(
        {"grade": "8", "currency": "INR"}
    )


def test_scope_key_drops_blank_qualifiers():
    """A qualifier the page never stated must not become part of the identity."""
    assert scope_key_for({"currency": "INR", "period": ""}) == "currency=inr"


def test_normalize_text_collapses_whitespace_and_bounds_length():
    assert normalize_text("  a\n\t b ") == "a b"
    assert len(normalize_text("x" * 5000)) <= 512


# =========================================================================
# Organization identity — the no-structured-data path
# =========================================================================
_HOME = """<html><head><title>Riverside Academy | Hill Town</title>
<meta name="description" content="A residential school"></head>
<body><h1>Riverside Academy</h1>
<a href="mailto:info@riverside.test?subject=Hello">Email</a>
<a href="tel:+91-135-000">Call</a></body></html>"""


def test_organization_is_established_without_any_structured_data(education):
    result = knowledge(
        education,
        _HOME,
        role="education.institution_home",
        url="https://a.test/",
        root=True,
    )
    assert entity_types(result) == {"education.organization"}
    assert result.entities[0].canonical_name == "Riverside Academy"
    assert "education.legal_name" in predicates(result)


def test_only_the_crawl_root_may_name_the_organization(education):
    """A section page's H1 must not rename the business."""
    result = knowledge(
        education,
        "<html><head><title>Fees</title></head>"
        "<body><h1>Fee Structure</h1></body></html>",
        role="education.institution_home",
        url="https://a.test/fees",
    )
    assert "education.legal_name" not in predicates(result)


def test_the_root_does_not_mint_a_campus_named_after_the_school(education):
    """``institution_home`` declares a campus type; the homepage is not one.

    Without the root exclusion the site's own name becomes a phantom place, and
    every later address and fee attaches to it instead of the organization.
    """
    result = knowledge(
        education,
        _HOME,
        role="education.institution_home",
        url="https://a.test/",
        root=True,
    )
    assert "education.campus" not in entity_types(result)


def test_missing_root_name_is_reported_as_a_warning_not_invented(education):
    result = knowledge(
        education,
        "<html><body><p>no title, no heading</p></body></html>",
        role="education.institution_home",
        url="https://a.test/",
        root=True,
    )
    assert "organization_name_absent_from_root" in result.warnings
    assert not result.assertions


def test_percent_escaped_mailto_yields_one_usable_address():
    """``mailto:%20info@x.test`` is one inbox, not an unusable second one.

    Observed live on the first acceptance corpus: the raw escape persisted as a
    contact point AND duplicated the real address.
    """
    facts = extract_page_facts(
        b"<html><body>"
        b"<a href='mailto:%20info@x.test'>a</a>"
        b"<a href='mailto:info@x.test'>b</a>"
        b"</body></html>",
        final_url="https://a.test/",
    )
    assert facts["contact_points"] == [{"channel": "email", "value": "info@x.test"}]


def test_contact_points_are_scoped_by_channel_and_deduplicated(education):
    result = knowledge(
        education,
        _HOME,
        role="education.institution_home",
        url="https://a.test/",
        root=True,
    )
    contacts = [
        assertion
        for assertion in result.assertions
        if assertion.predicate_id == "education.contact_point"
    ]
    assert {c.scope["channel"] for c in contacts} == {"email", "phone"}
    # The mailto query is template text, not part of the address.
    assert all("?" not in c.normalized_value for c in contacts)


# =========================================================================
# Role-driven entities
# =========================================================================
def test_a_program_page_is_the_offering_it_describes(education):
    result = knowledge(
        education,
        "<html><head><title>IGCSE</title></head>"
        "<body><h1>Cambridge IGCSE</h1></body></html>",
        role="education.program_detail",
        url="https://a.test/igcse",
    )
    assert "education.program" in entity_types(result)
    assert [r.relation_type_id for r in result.relations] == [
        "education.program_offered_by"
    ]


def test_role_entity_identity_is_the_page_not_its_heading(education):
    """Most pack types identify on fields a page cannot supply.

    ``education.admission_window`` identifies on
    ``(organization, academic_year, grade_scope)``. Keying it on the H1 would
    assert "Admissions" as a name the type does not have, and collapse two
    academic years' windows into one entity. The page is the identity.
    """
    result = knowledge(
        education,
        "<html><body><h1>Admissions</h1></body></html>",
        role="education.admissions_overview",
        url="https://a.test/admissions",
    )
    same_heading_other_url = knowledge(
        education,
        "<html><body><h1>Admissions</h1></body></html>",
        role="education.admissions_overview",
        url="https://a.test/apply",
    )
    entity = result.entities[0]

    # Same heading, different page: two entities, because the URL is identity.
    assert entity.ref != same_heading_other_url.entities[0].ref
    assert "admissions" in entity.ref.identity_key
    # The heading survives as a human label, not as identity.
    assert entity.canonical_name == "Admissions"


def test_a_redesign_that_rewrites_headings_keeps_the_same_entity(education):
    """Identity must survive copy changes, or every recrawl orphans its facts."""
    before = knowledge(
        education,
        "<html><body><h1>Senior School Fees</h1></body></html>",
        role="education.fees",
        url="https://a.test/fees",
    )
    after = knowledge(
        education,
        "<html><body><h1>Our 2027 Fee Structure</h1></body></html>",
        role="education.fees",
        url="https://a.test/fees",
    )
    assert before.entities[0].ref == after.entities[0].ref


def test_two_fee_pages_are_two_fee_schedules(education):
    day = knowledge(
        education,
        "<html><body><h1>Fees</h1></body></html>",
        role="education.fees",
        url="https://a.test/fees/day-scholar",
    )
    boarding = knowledge(
        education,
        "<html><body><h1>Fees</h1></body></html>",
        role="education.fees",
        url="https://a.test/fees/boarding",
    )
    assert day.entities[0].ref != boarding.entities[0].ref


def test_an_unclassified_page_contributes_no_role_entity(education):
    result = knowledge(
        education,
        "<html><body><h1>Something</h1></body></html>",
        role=None,
        url="https://a.test/x",
    )
    assert not result.entities


# =========================================================================
# Money — the fabrication guard
# =========================================================================
_FEES = """<html><head><title>Fees</title></head><body><h1>Senior School Fees</h1>
<p>Annual tuition fee is INR 2,50,000 per year.</p></body></html>"""


def test_fee_binds_to_the_pack_declared_subject_and_predicate(education):
    result = knowledge(
        education, _FEES, role="education.fees", url="https://a.test/fees"
    )
    fees = [a for a in result.assertions if a.value_type == "money"]

    assert len(fees) == 1
    assert fees[0].predicate_id == "education.fee_amount"
    assert fees[0].subject.entity_type_id == "education.fee_schedule"
    assert fees[0].currency == "INR"
    assert fees[0].numeric_value == 250000.0


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("INR 2,50,000", 250000.0),
        ("INR 250,000", 250000.0),
        ("INR 250000", 250000.0),
        # Paise must survive: truncating to 250000 understates a fee, and
        # discarding the mention entirely reports the page as naming no price.
        ("INR 2,50,000.75", 250000.75),
        # A non-breaking space is ordinary grouping on Indian and European
        # sites. The amount pattern accepts it, so the cleanup must strip it —
        # it used to reach ``float`` intact and drop the fee on ValueError.
        ("INR 2,50,000 ", 250000.0),
        ("INR 250 000", 250000.0),
    ],
)
def test_indian_and_western_grouping_both_parse_in_full(markup, expected):
    """``250000`` once parsed as 250 — a 250-rupee annual school fee."""
    facts = extract_page_facts(
        f"<html><body><p>Fee {markup} yearly</p></body></html>".encode(),
        final_url="https://a.test/",
        # Declared, because the non-ASCII separators below are UTF-8 bytes and a
        # mis-decode would make this test pass or fail for the wrong reason.
        charset="utf-8",
    )
    assert [m["amount"] for m in facts["money_mentions"]] == [expected]


def test_an_amount_without_a_currency_is_not_money():
    facts = extract_page_facts(
        b"<html><body><p>Capacity is 250000 students</p></body></html>",
        final_url="https://a.test/",
    )
    assert facts["money_mentions"] == []


def test_money_on_a_page_with_no_pack_subject_warns_instead_of_asserting(education):
    """A number on an events page is not a fee, and silence would hide that."""
    result = knowledge(
        education,
        "<html><body><h1>Sports Day</h1><p>Prize INR 5,000</p></body></html>",
        role="education.event_news",
        url="https://a.test/news",
    )
    assert not [a for a in result.assertions if a.value_type == "money"]
    assert "money_mentions_without_a_pack_declared_subject" in result.warnings


def test_unscoped_fee_records_only_the_qualifiers_the_page_states(education):
    """Required scope the page never stated is left OUT, never defaulted."""
    result = knowledge(
        education, _FEES, role="education.fees", url="https://a.test/fees"
    )
    fee = next(a for a in result.assertions if a.value_type == "money")

    assert set(fee.scope) == {"currency", "offering"}
    # academic_year / grade / fee_type / timing are required by the pack and
    # absent here: their absence is the finding.
    assert "academic_year" not in fee.scope


def test_money_amounts_in_script_bodies_never_become_fees():
    facts = extract_page_facts(
        b"<html><body><h1>Fees</h1>"
        b"<script>var p='INR 9,99,999';</script></body></html>",
        final_url="https://a.test/fees",
    )
    assert facts["money_mentions"] == []


# =========================================================================
# One extractor, two industries (the S4 gate)
# =========================================================================
def test_commerce_price_binds_to_the_offer_not_the_product(commerce):
    """schema.org separates a product from its offer, and so does the pack."""
    result = knowledge(
        commerce,
        "<html><head><title>Toothpaste</title></head>"
        "<body><h1>Herbal Toothpaste 100g</h1><p>Price USD 4.99</p></body></html>",
        role="commerce.product_detail",
        url="https://s.test/p/herbal",
    )
    price = next(a for a in result.assertions if a.value_type == "money")

    assert entity_types(result) == {"commerce.product", "commerce.offer"}
    assert price.subject.entity_type_id == "commerce.offer"
    assert price.predicate_id == "commerce.price"
    assert "commerce.offer_applies_to" in {
        relation.relation_type_id for relation in result.relations
    }


def test_the_offer_shares_the_products_identity_not_a_second_page(commerce):
    result = knowledge(
        commerce,
        "<html><body><h1>Herbal Toothpaste 100g</h1><p>USD 4.99</p></body></html>",
        role="commerce.product_detail",
        url="https://s.test/p/herbal",
    )
    assert len(result.entities) == 2
    assert len({entity.ref.identity_key for entity in result.entities}) == 1


# =========================================================================
# Determinism (the S2 gate)
# =========================================================================
def test_identical_facts_reproduce_identical_knowledge(education):
    first = knowledge(
        education, _FEES, role="education.fees", url="https://a.test/fees"
    )
    second = knowledge(
        education, _FEES, role="education.fees", url="https://a.test/fees"
    )
    assert first == second


def test_an_unpacked_crawl_produces_nothing_and_says_why(education):
    """No pack means no vocabulary — not an empty business."""
    empty = compile_vocabulary({"pack_id": "", "version": ""})
    result = knowledge(empty, _HOME, role=None, url="https://a.test/", root=True)

    assert not result.entities
    assert result.warnings == ("no_organization_entity_type",)
