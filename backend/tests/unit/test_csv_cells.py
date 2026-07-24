"""Shared CSV/MD cell helpers (app/analysis/csv_cells.py).

Single owner of the spreadsheet formula-injection neutralization used by the
site-health and product-visibility exports (invariant 2: import, never copy).
"""

from __future__ import annotations

import pytest

from app.analysis.csv_cells import csv_cell, has_formula_trigger, stringify_cell


def test_stringify_cell_handles_none_bool_and_numbers() -> None:
    assert stringify_cell(None) == ""
    assert stringify_cell(True) == "true"
    assert stringify_cell(False) == "false"
    assert stringify_cell(0) == "0"
    assert stringify_cell(0.0) == "0.0"
    assert stringify_cell("text") == "text"


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_has_formula_trigger_catches_leading_triggers(trigger: str) -> None:
    assert has_formula_trigger(f"{trigger}SUM(A1:A2)") is True


@pytest.mark.parametrize("trigger", ["=", "+", "-", "@"])
def test_has_formula_trigger_catches_whitespace_hidden_triggers(trigger: str) -> None:
    assert has_formula_trigger(f"  {trigger}1+1") is True
    assert has_formula_trigger(f"\t{trigger}1+1") is True


def test_has_formula_trigger_allows_safe_text() -> None:
    assert has_formula_trigger("") is False
    assert has_formula_trigger("Acme VoltBike 500") is False
    assert has_formula_trigger("price -10% off") is False  # not leading


def test_csv_cell_prefixes_formula_cells_with_quote() -> None:
    assert (
        csv_cell('=HYPERLINK("https://evil","x")') == '\'=HYPERLINK("https://evil","x")'
    )
    assert csv_cell("\t=1+1") == "'\t=1+1"
    assert csv_cell("safe") == "safe"
    assert csv_cell(None) == ""
