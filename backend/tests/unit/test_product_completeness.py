"""Unit tests for the per-SKU data-quality completeness matrix (pure, on read)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config.products import (
    PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS,
    PRODUCT_REQUIRED_ATTRIBUTES,
)
from app.domain.products.completeness import product_completeness

_TOTAL = len(PRODUCT_REQUIRED_ATTRIBUTES) + len(PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS)


@dataclass
class _Product:
    sku: str = ""
    name: str = ""
    price: float | None = None
    currency: str = ""
    url: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


def _full_attributes() -> dict[str, str]:
    return {key: f"value-{key}" for key in PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS}


def test_complete_row_scores_full() -> None:
    product = _Product(
        sku="VC-500",
        name="VoltCity 500",
        price=2499.0,
        currency="USD",
        url="https://acme.com/p/vc500",
        attributes=_full_attributes(),
    )
    result = product_completeness(product)
    assert result["score"] == 1.0
    assert result["present"] == _TOTAL
    assert result["total"] == _TOTAL
    assert result["missing"] == []


def test_empty_catalog_row_misses_everything() -> None:
    result = product_completeness(_Product())
    assert result["score"] == 0.0
    assert result["present"] == 0
    assert result["total"] == _TOTAL
    # Top-level fields come first, attribute keys after, in matrix order.
    assert result["missing"] == list(PRODUCT_REQUIRED_ATTRIBUTES) + list(
        PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS
    )


def test_partial_row_reports_missing_in_matrix_order() -> None:
    product = _Product(
        sku="VC-500",
        name="VoltCity 500",
        price=None,  # missing
        currency="USD",
        url="  ",  # blank counts as missing
        attributes={"brand": "Voltaic", "category": "E-Bikes"},
    )
    result = product_completeness(product)
    expected_missing = ["price", "url"] + [
        key
        for key in PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS
        if key not in {"brand", "category"}
    ]
    assert result["missing"] == expected_missing
    assert result["present"] == _TOTAL - len(expected_missing)
    assert result["score"] == round(result["present"] / _TOTAL, 4)


def test_whitespace_and_none_attribute_values_count_missing() -> None:
    product = _Product(
        sku="VC-500",
        name="VoltCity 500",
        price=1.0,
        currency="USD",
        url="https://acme.com",
        attributes={**_full_attributes(), "gtin": "  ", "mpn": None},
    )
    result = product_completeness(product)
    assert result["missing"] == ["gtin", "mpn"]


def test_missing_attributes_bag_tolerated() -> None:
    product = _Product(
        sku="VC-500",
        name="VoltCity 500",
        price=1.0,
        currency="USD",
        url="https://acme.com",
        attributes=None,  # type: ignore[assignment]
    )
    result = product_completeness(product)
    assert result["missing"] == list(PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS)
