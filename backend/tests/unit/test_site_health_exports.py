"""Unit tests for the Site Health CSV/Markdown exporters (pure renderers).

The exporters render already-projected dict rows into RFC-4180 CSV and safe
Markdown tables. They never touch the DB, so these tests cover: header/column
ordering, RFC-4180 quoting of embedded delimiters/quotes/newlines, Markdown
cell escaping (``|``, ``\\``, newline collapse), boolean/None cell rendering,
and the always-valid empty table.
"""
from __future__ import annotations

import csv
import io

import pytest

from app.analysis.site_health.exports import (
    _VIEW_COLUMNS,
    EXPORT_VIEWS,
    rows_to_csv,
    rows_to_markdown,
)


def _parse_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_export_views_cover_the_three_surfaces() -> None:
    assert EXPORT_VIEWS == frozenset({"inventory", "pages", "issues"})


@pytest.mark.parametrize("view", sorted(EXPORT_VIEWS))
def test_csv_header_matches_view_columns(view: str) -> None:
    text = rows_to_csv(view, [])
    rows = _parse_csv(text)
    assert rows[0] == _VIEW_COLUMNS[view]


def test_csv_renders_bool_and_none_cells() -> None:
    items = [
        {
            "site_url_id": "abc",
            "normalized_url": "https://x/",
            "display_url": "https://x/",
            "title": None,
            "content_type": "text/html",
            "source": "sitemap",
            "depth": 0,
            "monitored": True,
            "issue_count": None,
            "technical_score": None,
            "aeo_score": None,
            "overall_score": None,
            "last_audited": None,
        }
    ]
    rows = _parse_csv(rows_to_csv("inventory", items))
    data = dict(zip(rows[0], rows[1], strict=True))
    # None -> empty string; bool -> lowercase literal.
    assert data["title"] == ""
    assert data["issue_count"] == ""
    assert data["monitored"] == "true"


def test_csv_rfc4180_quoting_of_delimiters_quotes_newlines() -> None:
    items = [
        {
            "id": "1",
            "rule_id": "technical.title_present",
            "title": 'A "quoted", comma title',
            "dimension": "technical",
            "category": "meta",
            "severity": "critical",
            "affected_url_count": 3,
            "remediation": "line one\nline two",
            "analyzer_version": "v1",
            "rule_version": "v1",
            "created_at": "2026-01-01T00:00:00Z",
        }
    ]
    text = rows_to_csv("issues", items)
    rows = _parse_csv(text)
    data = dict(zip(rows[0], rows[1], strict=True))
    # csv round-trips the raw values intact.
    assert data["title"] == 'A "quoted", comma title'
    assert data["remediation"] == "line one\nline two"


def test_markdown_always_emits_title_header_separator_even_when_empty() -> None:
    md = rows_to_markdown("pages", [])
    lines = md.splitlines()
    assert lines[0].startswith("# ")
    # Header row + separator row present with the right column count.
    header_idx = next(i for i, ln in enumerate(lines) if ln.startswith("|"))
    header = lines[header_idx]
    separator = lines[header_idx + 1]
    ncols = len(_VIEW_COLUMNS["pages"])
    assert header.count("|") == ncols + 1
    assert set(separator.replace("|", "").replace("-", "").strip()) == set()


def test_markdown_escapes_pipes_backslashes_and_collapses_newlines() -> None:
    items = [
        {
            "id": "1",
            "rule_id": "r",
            "title": "a | b \\ c",
            "dimension": "technical",
            "category": "meta",
            "severity": "info",
            "affected_url_count": 1,
            "remediation": "fix\nthis\r\nnow",
            "analyzer_version": "v1",
            "rule_version": "v1",
            "created_at": "2026-01-01T00:00:00Z",
        }
    ]
    md = rows_to_markdown("issues", items)
    # The data row must escape the pipe/backslash and collapse the newlines.
    data_line = next(
        ln for ln in md.splitlines() if "fix" in ln and ln.startswith("|")
    )
    assert "\\|" in data_line
    assert "\\\\" in data_line
    assert "\n" not in data_line.replace("|", "")
    assert "\r" not in data_line
    # Newlines are collapsed to spaces; no raw line break leaks into the cell.
    assert "fix" in data_line and "now" in data_line
