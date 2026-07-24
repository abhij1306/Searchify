"""Unit tests for the deterministic product scorer (table tests, invariant 9).

Covers name/alias/SKU/variant matching incl. boundary safety, price
extraction ($/EUR/GBP/ISO-code forms) + tolerance edges + currency-mismatch,
rank detection across numbered/bullet/table enumerations, aggregate SOV math,
and same-input determinism. Pure functions only — no DB.
"""

from __future__ import annotations

import pytest

from app.analysis.product_scoring import (
    ProductEntry,
    ProductScoringConfig,
    aggregate_product_run,
    detect_product_rank,
    extract_price_mentions,
    price_matches_catalog,
    score_product_execution,
)
from app.core.config.products import PRODUCT_RANK_BUCKETS

_BUCKET_LABELS = [label for label, _, _ in PRODUCT_RANK_BUCKETS] + ["unranked"]


def _config(**overrides) -> ProductScoringConfig:
    base = {
        "products": [
            {
                "id": "p1",
                "sku": "VC-EB500-GR",
                "name": "VoltCity Commuter 500",
                "aliases": ["VoltCity 500"],
                "variants": [
                    {
                        "name": "Graphite / Standard",
                        "sku": "VC-EB500-GR",
                        "price": 2499.0,
                    }
                ],
                "price": 2499.0,
                "currency": "USD",
                "url": "https://acme.com/p/vc500",
            },
            {
                "id": "p2",
                "sku": "SF-200W",
                "name": "SolarFold Panel 200W",
                "aliases": [],
                "variants": [],
                "price": None,
                "currency": "",
                "url": "",
            },
        ],
        "competitor_products": [
            {
                "id": "c1",
                "competitor_id": "comp-1",
                "competitor_name": "RideCore",
                "name": "RideCore CityCommuter 450",
                "aliases": ["CityCommuter"],
                "price": 2399.0,
                "currency": "USD",
            }
        ],
    }
    base.update(overrides)
    return ProductScoringConfig.from_project(base)


# --------------------------------------------------------------------------
# from_project + matching
# --------------------------------------------------------------------------
def test_from_project_folds_name_sku_aliases_variants_into_match_set() -> None:
    config = _config()
    entry = config.products[0]
    assert entry.id == "p1"
    assert set(entry.aliases) == {
        "VoltCity Commuter 500",
        "VC-EB500-GR",
        "VoltCity 500",
        "Graphite / Standard",
    }
    assert entry.price == 2499.0
    assert entry.currency == "USD"
    # Competitor products match on name + aliases only.
    assert set(config.competitor_products[0].aliases) == {
        "RideCore CityCommuter 450",
        "CityCommuter",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The VoltCity Commuter 500 is great.", True),  # name
        ("the voltcity commuter 500 is great.", True),  # case-insensitive
        ("Consider the VoltCity 500 today.", True),  # alias
        ("SKU VC-EB500-GR ships fast.", True),  # sku
        ("Also written VC EB500 GR in prose.", True),  # sku punctuation-tolerant
        ("The Graphite / Standard trim sells out.", True),  # variant name
        ("VoltCity Commuter 5000 is a different model.", False),  # boundary
        ("VC-EB500-GRX is not the sku.", False),  # boundary
        ("Nothing relevant here.", False),
    ],
)
def test_product_matching_and_boundaries(text: str, expected: bool) -> None:
    score = score_product_execution(answer_text=text, config=_config())
    assert score["products"][0]["mentioned"] is expected


def test_empty_catalog_scores_nothing() -> None:
    config = ProductScoringConfig.from_project({})
    score = score_product_execution(answer_text="VoltCity 500", config=config)
    assert score == {
        "products": [],
        "competitor_products": [],
        "own_product_mention_count": 0,
        "competitor_product_mention_count": 0,
        "products_with_price_match": 0,
    }


# --------------------------------------------------------------------------
# Price extraction + matching
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "value", "currency"),
    [
        ("it costs $2,499.00 today", 2499.00, "USD"),
        ("priced at US$ 49.99 now", 49.99, "USD"),
        ("only 2499 USD here", 2499.0, "USD"),
        ("€499.00 in europe", 499.00, "EUR"),
        ("£899 in the uk", 899.0, "GBP"),
        ("A$649 down under", 649.0, "AUD"),
        ("about 1.149,00 EUR", None, ""),  # unsupported decimal format
        ("no price at all", None, ""),
        ("the S$5 marker is unknown", None, ""),  # unconfigured currency
    ],
)
def test_extract_price_mentions(text: str, value: float | None, currency: str) -> None:
    mentions = extract_price_mentions(text, offset=text.find(" "), window=400)
    if value is None:
        assert mentions == []
    else:
        assert mentions[0]["value"] == value
        assert mentions[0]["currency"] == currency


def test_extract_price_prefers_first_in_line_and_stays_in_line() -> None:
    text = "1. VoltCity 500 at $2,499.00 or $2,449.00\n2. RideCore 450 at $2,399.00"
    first_line_offset = text.find("VoltCity")
    mentions = extract_price_mentions(text, first_line_offset)
    assert [m["value"] for m in mentions] == [2499.00, 2449.00]
    # The neighbouring line's price is not misattributed.
    second_line_offset = text.find("RideCore")
    mentions = extract_price_mentions(text, second_line_offset)
    assert [m["value"] for m in mentions] == [2399.00]


@pytest.mark.parametrize(
    ("mentioned", "currency", "expected"),
    [
        (2499.0, "USD", True),  # exact
        (2499.0 + 124.95, "USD", True),  # at pct-tolerance edge
        (2499.0 + 124.96, "USD", False),  # just outside
        (2499.0, "EUR", None),  # currency mismatch -> not verifiable
        (2499.0, "", True),  # unknown mentioned currency -> value compare
    ],
)
def test_price_matches_catalog_tolerance_edges(
    mentioned: float, currency: str, expected: bool | None
) -> None:
    entry = ProductEntry(
        id="p1", sku="S", name="N", aliases=(), price=2499.0, currency="USD"
    )
    assert price_matches_catalog(mentioned, currency, entry) is expected


def test_price_matches_catalog_abs_floor_and_missing_catalog_price() -> None:
    cheap = ProductEntry(
        id="p1", sku="S", name="N", aliases=(), price=10.0, currency="USD"
    )
    assert price_matches_catalog(11.0, "USD", cheap) is True  # abs floor 1.0
    assert price_matches_catalog(11.01, "USD", cheap) is False
    priceless = ProductEntry(
        id="p2", sku="S", name="N", aliases=(), price=None, currency=""
    )
    assert price_matches_catalog(2499.0, "USD", priceless) is None


# --------------------------------------------------------------------------
# Rank detection
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "needle", "expected"),
    [
        ("1. VoltCity 500\n2. RideCore 450", "VoltCity 500", 1),
        ("1. VoltCity 500\n2. RideCore 450", "RideCore 450", 2),
        ("1) VoltCity 500\n2) RideCore 450", "RideCore 450", 2),
        ("- VoltCity 500\n- RideCore 450", "RideCore 450", 2),
        ("• VoltCity 500\n• RideCore 450", "VoltCity 500", 1),
        # Wrapped item: the continuation line belongs to item 2.
        (
            "1. VoltCity 500\n2. RideCore 450 is a great\noption for commuters",
            "commuters",
            2,
        ),
        # Prose mention outside any enumeration.
        ("The VoltCity 500 is widely recommended.", "VoltCity 500", None),
        # A restarted numbered list is a new block (rank restarts).
        ("1. VoltCity 500\n\n1. RideCore 450", "RideCore 450", 1),
        # Markdown table rows enumerate data rows (header/separator skipped).
        (
            (
                "| Rank | Product |\n| --- | --- |\n"
                "| 1 | VoltCity 500 |\n| 2 | RideCore 450 |"
            ),
            "RideCore 450",
            2,
        ),
        (
            (
                "| Rank | Product |\n| --- | --- |\n"
                "| 1 | VoltCity 500 |\n| 2 | RideCore 450 |"
            ),
            "VoltCity 500",
            1,
        ),
        # Headerless table (no separator row): nothing to skip, so the first
        # row IS rank 1 rather than being dropped as a header.
        ("| VoltCity 500 |\n| RideCore 450 |", "VoltCity 500", 1),
        ("| VoltCity 500 |\n| RideCore 450 |", "RideCore 450", 2),
        # Single-row table: still a ranked mention, not None.
        ("| VoltCity 500 | $2,499 |", "VoltCity 500", 1),
    ],
)
def test_detect_product_rank(text: str, needle: str, expected: int | None) -> None:
    assert detect_product_rank(text, text.find(needle)) == expected


def test_multiple_products_get_distinct_ranks() -> None:
    text = (
        "1. RideCore CityCommuter 450 — $2,399\n"
        "2. VoltCity Commuter 500 — $2,499.00\n"
        "3. SolarFold Panel 200W — $499"
    )
    score = score_product_execution(answer_text=text, config=_config())
    own = {p["product_id"]: p for p in score["products"]}
    competitor = score["competitor_products"][0]
    assert competitor["rank_position"] == 1
    assert own["p1"]["rank_position"] == 2
    assert own["p2"]["rank_position"] == 3
    assert own["p2"]["price_matches_catalog"] is None  # no catalog price
    assert score["own_product_mention_count"] == 2
    assert score["competitor_product_mention_count"] == 1
    assert score["products_with_price_match"] == 2  # p1 + c1 (p2 unverifiable)


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
def test_aggregate_product_run_sov_and_rates() -> None:
    config = _config()
    text_a = (
        "1. VoltCity Commuter 500 — $2,499.00\n2. RideCore CityCommuter 450 — $2,399"
    )
    text_b = "The RideCore CityCommuter 450 ($2,100 sale) beats the field."
    scores = [
        score_product_execution(answer_text=text, config=config)
        for text in (text_a, text_b)
    ]
    aggregates = aggregate_product_run(scores, config)

    own = aggregates["p1"]
    assert own["kind"] == "product"
    assert own["mention_count"] == 1
    # 1 own mention over 3 total mentions (1 own + 2 competitor).
    assert own["sov_share"] == pytest.approx(1 / 3, abs=1e-4)
    assert own["avg_rank"] == 1.0
    assert own["rank_distribution"]["top_1"] == 1
    assert own["price_accuracy_rate"] == 1.0

    competitor = aggregates["c1"]
    assert competitor["mention_count"] == 2
    assert competitor["sov_share"] == pytest.approx(2 / 3, abs=1e-4)
    # Ranked once (rank 2), unranked once.
    assert competitor["avg_rank"] == 2.0
    assert competitor["rank_distribution"]["top_2_3"] == 1
    assert competitor["rank_distribution"]["unranked"] == 1
    # Two verifiable price mentions; the $2,100 sale price mismatches.
    assert competitor["price_mention_count"] == 2
    assert competitor["price_match_count"] == 1
    assert competitor["price_accuracy_rate"] == 0.5

    # p2 never mentioned: zero-filled aggregate with nulls, not an error.
    empty = aggregates["p2"]
    assert empty["mention_count"] == 0
    assert empty["sov_share"] == 0.0
    assert empty["avg_rank"] is None
    assert empty["price_accuracy_rate"] is None
    assert empty["rank_distribution"] == {label: 0 for label in _BUCKET_LABELS}


def test_aggregate_product_run_all_empty() -> None:
    aggregates = aggregate_product_run([], _config())
    assert set(aggregates) == {"p1", "p2", "c1"}
    for aggregate in aggregates.values():
        assert aggregate["mention_count"] == 0
        assert aggregate["sov_share"] == 0.0
        assert aggregate["avg_rank"] is None
        assert aggregate["price_mention_count"] == 0
        assert aggregate["price_accuracy_rate"] is None


def test_score_product_execution_deterministic() -> None:
    config = _config()
    text = "1. VoltCity Commuter 500 — $2,499.00\n2. RideCore CityCommuter 450 — $2,399"
    first = score_product_execution(answer_text=text, config=config)
    second = score_product_execution(answer_text=text, config=config)
    assert first == second
    assert aggregate_product_run([first], config) == aggregate_product_run(
        [second], config
    )
