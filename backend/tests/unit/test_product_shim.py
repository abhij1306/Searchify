"""Unit test for the product-catalog serialization shim.

Mirrors ``test_project_shim.py``: the deterministic product scorer consumes a
plain dict via ``ProductScoringConfig.from_project``; this shim rebuilds that
dict from the normalized ``Product`` / ``CompetitorProduct`` rows so the
planner can freeze it into every audit's ``configuration``. Asserts the dict
shape, empty-catalog behavior, price coercion (Decimal -> float, ids -> str),
and the no-duplication contract: the shim does NOT fold name/sku/variant into
``aliases`` (the scorer builds its own match-alias set — the same class of
bug as duplicating ``brand_name`` into ``brand_aliases``).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.domain.products.shim import project_product_identity
from app.models.brand import Competitor
from app.models.product import CompetitorProduct, Product
from app.models.project import Project


def _sample_project() -> Project:
    project = Project(name="Voltaic", brand_name="Voltaic Supply")
    project.products = [
        Product(
            id=uuid.uuid4(),
            sku="VC-EB500-GR",
            name="VoltCity Commuter 500",
            aliases=["VoltCity 500"],
            variants=[
                {"name": "Graphite / Standard", "sku": "VC-EB500-GR", "price": 2499.0}
            ],
            price=Decimal("2499.00"),
            currency="USD",
            url="https://voltaic.example/products/voltcity-500",
        ),
        Product(
            id=uuid.uuid4(),
            sku="SF-200W",
            name="SolarFold Panel 200W",
            price=None,
            currency="",
            url="",
        ),
    ]
    competitor = Competitor(id=uuid.uuid4(), name="RideCore")
    project.competitor_products = [
        CompetitorProduct(
            id=uuid.uuid4(),
            competitor_id=competitor.id,
            competitor=competitor,
            name="RideCore CityCommuter 450",
            aliases=["CityCommuter"],
            price=Decimal("2399.00"),
            currency="USD",
        )
    ]
    return project


def test_shim_produces_expected_dict_shape() -> None:
    project = _sample_project()
    identity = project_product_identity(project)

    assert set(identity) == {"products", "competitor_products"}
    own, imported = identity["products"]
    assert own == {
        "id": str(project.products[0].id),
        "sku": "VC-EB500-GR",
        "name": "VoltCity Commuter 500",
        "aliases": ["VoltCity 500"],
        "variants": [
            {"name": "Graphite / Standard", "sku": "VC-EB500-GR", "price": 2499.0}
        ],
        "price": 2499.00,
        "currency": "USD",
        "url": "https://voltaic.example/products/voltcity-500",
    }
    # Prices coerce to floats, ids to strings.
    assert isinstance(own["price"], float)
    assert isinstance(own["id"], str)
    # The shim does NOT fold name/sku/variant into aliases (scorer's job).
    assert own["aliases"] == ["VoltCity 500"]
    # Missing price/currency stay null/empty (never fabricated).
    assert imported["price"] is None
    assert imported["currency"] == ""
    assert imported["aliases"] == []
    assert imported["variants"] == []

    competitor_product = identity["competitor_products"][0]
    assert competitor_product == {
        "id": str(project.competitor_products[0].id),
        "competitor_id": str(project.competitor_products[0].competitor_id),
        "competitor_name": "RideCore",
        "name": "RideCore CityCommuter 450",
        "aliases": ["CityCommuter"],
        "price": 2399.00,
        "currency": "USD",
    }
    assert isinstance(competitor_product["price"], float)


def test_shim_empty_catalog_yields_empty_lists() -> None:
    identity = project_product_identity(Project(name="Bare"))
    assert identity == {"products": [], "competitor_products": []}


def test_shim_tolerates_missing_competitor_and_junk_variants() -> None:
    project = Project(name="Edge")
    project.products = [
        Product(
            id=uuid.uuid4(),
            sku="X-1",
            name="Edge Product",
            variants=["not-a-dict", {"name": "Only Name"}],
        )
    ]
    orphan = CompetitorProduct(
        id=uuid.uuid4(), competitor_id=uuid.uuid4(), name="Orphan"
    )
    project.competitor_products = [orphan]

    identity = project_product_identity(project)
    assert identity["products"][0]["variants"] == [
        {"name": "Only Name", "sku": "", "price": None}
    ]
    assert identity["competitor_products"][0]["competitor_name"] == ""
