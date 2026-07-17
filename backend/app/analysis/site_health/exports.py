# Site Health persisted-data exports (Slice 6, workspace-safe).
#
# Pure renderers over ALREADY-PROJECTED dicts (the exact shapes the service
# layer produces for the inventory / pages / issues views). They never re-score,
# never fetch, and never read a raw body — the router hands them the same
# workspace-scoped projections the JSON API returns, so an export can never leak
# more than the API. CSV escaping is delegated to the stdlib ``csv`` writer;
# Markdown cell content is escaped so a URL/title containing ``|`` or a newline
# can never break the table. Empty result sets still render a valid header row
# / empty table, and ``None`` renders as an empty cell.
from __future__ import annotations

import csv
import io
from typing import Any

_INVENTORY_COLUMNS = [
    "site_url_id",
    "normalized_url",
    "display_url",
    "title",
    "content_type",
    "source",
    "depth",
    "monitored",
    "issue_count",
    "technical_score",
    "aeo_score",
    "overall_score",
    "last_audited",
]

_PAGES_COLUMNS = [
    "site_url_id",
    "normalized_url",
    "display_url",
    "title",
    "monitored",
    "analysis_status",
    "error_code",
    "issue_count",
    "technical_score",
    "aeo_score",
    "overall_score",
    "last_audited",
]

_ISSUES_COLUMNS = [
    "id",
    "rule_id",
    "title",
    "dimension",
    "category",
    "severity",
    "affected_url_count",
    "remediation",
    "analyzer_version",
    "rule_version",
    "created_at",
]

_VIEW_COLUMNS: dict[str, list[str]] = {
    "inventory": _INVENTORY_COLUMNS,
    "pages": _PAGES_COLUMNS,
    "issues": _ISSUES_COLUMNS,
}

EXPORT_VIEWS = frozenset(_VIEW_COLUMNS)


def _cell(value: Any) -> str:
    """Stringify a value for a CSV/MD cell (``None``/absent -> empty string)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def rows_to_csv(view: str, items: list[dict]) -> str:
    """Render projected ``items`` for ``view`` as CSV (RFC-4180 via stdlib).

    The stdlib writer quotes/escapes any cell containing a comma, quote, or
    newline, so a URL or remediation string can never break the columns.
    """
    columns = _VIEW_COLUMNS[view]
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=columns, extrasaction="ignore"
    )
    writer.writeheader()
    for item in items:
        writer.writerow({col: _cell(item.get(col)) for col in columns})
    return buffer.getvalue()


def _md_cell(value: Any) -> str:
    """Escape a value so it is a single safe Markdown table cell.

    Pipes are escaped and newlines collapsed so a multi-line remediation or a
    URL containing ``|`` cannot break the table row.
    """
    text = _cell(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
    )


_VIEW_TITLES: dict[str, str] = {
    "inventory": "Site Health — URL Inventory",
    "pages": "Site Health — Analyzed Pages",
    "issues": "Site Health — Issues",
}


def rows_to_markdown(view: str, items: list[dict]) -> str:
    """Render projected ``items`` for ``view`` as a Markdown table.

    Always emits the title + header + separator, so an empty result set is a
    valid (empty) table rather than a broken document.
    """
    columns = _VIEW_COLUMNS[view]
    lines = [f"# {_VIEW_TITLES.get(view, 'Site Health Export')}", ""]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join(["---"] * len(columns)) + "|")
    for item in items:
        lines.append(
            "| " + " | ".join(_md_cell(item.get(col)) for col in columns) + " |"
        )
    lines.append("")
    return "\n".join(lines)
