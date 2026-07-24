"""Unit tests for the opportunities CSV/Markdown export renderers.

Pure renderers over projected dicts: the contract is (a) a stable header row
even for empty inputs, (b) spreadsheet-formula injection neutralization, and
(c) Markdown table escaping. Mirrors the site-health export tests.
"""

from __future__ import annotations

import csv
import io

from app.analysis.opportunities.exports import (
    OPPORTUNITIES_COLUMNS,
    rows_to_csv,
    rows_to_markdown,
)


def _item(**overrides: object) -> dict:
    base: dict = {
        "id": "6f5f2b6d-3f3f-4d2c-9d7a-7f1f5f7b9a01",
        "rule_id": "brand_absent_high_value_prompt",
        "opportunity_type": "visibility",
        "severity": "high",
        "priority_score": 60.0,
        "status": "open",
        "title": "Brand absent from high-value prompt",
        "target": "best crm for small teams",
        "remediation": "Publish a comparison page.",
        "rule_version": "opp-rules-1",
        "formula_version": "opp-formula-1",
        "created_at": "2026-07-24T00:00:00+00:00",
    }
    base.update(overrides)
    return base


class TestCsv:
    def test_empty_items_render_header_only(self) -> None:
        output = rows_to_csv([])
        rows = list(csv.reader(io.StringIO(output)))
        assert rows == [OPPORTUNITIES_COLUMNS]

    def test_row_values_round_trip(self) -> None:
        output = rows_to_csv([_item()])
        rows = list(csv.reader(io.StringIO(output)))
        assert len(rows) == 2
        record = dict(zip(OPPORTUNITIES_COLUMNS, rows[1], strict=True))
        assert record["rule_id"] == "brand_absent_high_value_prompt"
        assert record["priority_score"] == "60.0"
        assert record["target"] == "best crm for small teams"

    def test_missing_and_none_values_render_empty(self) -> None:
        output = rows_to_csv([{"rule_id": "thin_content", "target": None}])
        rows = list(csv.reader(io.StringIO(output)))
        record = dict(zip(OPPORTUNITIES_COLUMNS, rows[1], strict=True))
        assert record["rule_id"] == "thin_content"
        assert record["target"] == ""
        assert record["title"] == ""

    def test_formula_trigger_prefixed_with_quote(self) -> None:
        for dangerous in ("=HYPERLINK()", "+cmd", "-2+3", "@SUM(A1)"):
            output = rows_to_csv([_item(title=dangerous)])
            rows = list(csv.reader(io.StringIO(output)))
            record = dict(zip(OPPORTUNITIES_COLUMNS, rows[1], strict=True))
            assert record["title"] == "'" + dangerous

    def test_whitespace_hidden_formula_trigger_caught(self) -> None:
        output = rows_to_csv([_item(title="  =1+1")])
        rows = list(csv.reader(io.StringIO(output)))
        record = dict(zip(OPPORTUNITIES_COLUMNS, rows[1], strict=True))
        assert record["title"] == "'  =1+1"

    def test_leading_tab_or_newline_caught(self) -> None:
        for dangerous in ("\t=1+1", "\n=1+1", "\r=1+1"):
            output = rows_to_csv([_item(title=dangerous)])
            rows = list(csv.reader(io.StringIO(output)))
            record = dict(zip(OPPORTUNITIES_COLUMNS, rows[1], strict=True))
            assert record["title"].startswith("'")

    def test_safe_leading_text_not_quoted(self) -> None:
        output = rows_to_csv([_item(title="a = b, not a formula")])
        rows = list(csv.reader(io.StringIO(output)))
        record = dict(zip(OPPORTUNITIES_COLUMNS, rows[1], strict=True))
        assert record["title"] == "a = b, not a formula"

    def test_extra_keys_ignored(self) -> None:
        output = rows_to_csv([_item(secret="not-exported")])
        assert "not-exported" not in output


class TestMarkdown:
    def test_empty_items_render_valid_table(self) -> None:
        output = rows_to_markdown([])
        lines = output.splitlines()
        assert lines[0] == "# Searchify — Opportunities"
        assert lines[2] == "| " + " | ".join(OPPORTUNITIES_COLUMNS) + " |"
        assert set(lines[3]) <= {"|", "-"}

    def test_row_rendered(self) -> None:
        output = rows_to_markdown([_item()])
        assert "brand_absent_high_value_prompt" in output
        assert "best crm for small teams" in output

    def test_pipe_and_backslash_escaped(self) -> None:
        output = rows_to_markdown([_item(title="a | b \\ c")])
        row = [line for line in output.splitlines() if "a " in line][0]
        assert "a \\| b \\\\ c" in row

    def test_newlines_collapsed_to_space(self) -> None:
        output = rows_to_markdown([_item(title="line one\nline two")])
        assert "line one line two" in output
        # One table row only — the newline must not split the table.
        body = [line for line in output.splitlines() if "line one" in line]
        assert len(body) == 1

    def test_formula_trigger_neutralized(self) -> None:
        output = rows_to_markdown([_item(title="=1+1")])
        assert "'=1+1" in output
