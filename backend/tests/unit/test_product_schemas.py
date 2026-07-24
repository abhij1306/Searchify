"""Unit tests for the product-catalog schema validators.

Covers the two normalization contracts that are easy to get backwards:
currency must be trimmed BEFORE the ``max_length=3`` check, and a non-list
``aliases`` payload must fail validation rather than silently becoming ``[]``
(which would erase stored aliases on update).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.products.schemas import ProductInput, ProductUpdate


def test_currency_is_trimmed_and_uppercased_before_length_check() -> None:
    # " usd " is 5 chars raw: an after-validator would reject it on max_length
    # before ever trimming.
    assert (
        ProductInput(sku="VC-500", name="VoltCity", currency=" usd ").currency == "USD"
    )
    assert ProductUpdate(currency=" eur ").currency == "EUR"


def test_currency_over_three_chars_still_rejected() -> None:
    with pytest.raises(ValidationError):
        ProductInput(sku="VC-500", name="VoltCity", currency="DOLLARS")


def test_aliases_list_is_cleaned() -> None:
    product = ProductInput(
        sku="VC-500", name="VoltCity", aliases=["  VoltCity 500 ", "", "  "]
    )
    assert product.aliases == ["VoltCity 500"]


def test_non_list_aliases_is_a_validation_error_not_silent_empty() -> None:
    # Silently coercing to [] would erase stored aliases on a PATCH.
    with pytest.raises(ValidationError):
        ProductInput(sku="VC-500", name="VoltCity", aliases="VoltCity 500")
    with pytest.raises(ValidationError):
        ProductUpdate(aliases="VoltCity 500")


def test_update_aliases_none_still_means_unset() -> None:
    assert ProductUpdate(aliases=None).aliases is None
