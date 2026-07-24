# Opportunities persisted-data exports (workspace-safe).
#
# Pure renderers over ALREADY-PROJECTED dicts (the exact shape the service
# layer produces for the opportunities list). They never re-score, never
# fetch, and never read a raw body — the router hands them the same
# workspace-scoped projection the JSON API returns, so an export can never
# leak more than the API. CSV quoting/escaping is delegated to the stdlib
# ``csv`` writer; cell stringification, spreadsheet-formula neutralization,
# and Markdown cell escaping are SHARED with Site Health — the single
# implementation lives in ``analysis/site_health/exports.py`` (a cell
# beginning with a formula trigger is prefixed with ``'`` so a title
# containing ``|`` or a newline can never break the table or evaluate as a
# formula). Empty result sets still render a valid header row.
from __future__ import annotations

import csv
import io

# Shared cell renderers (RFC-4180 + OWASP formula neutralization + Markdown
# escaping) — owned by the Site Health exporters, reused here verbatim.
from app.analysis.site_health.exports import _csv_cell, _md_cell

OPPORTUNITIES_COLUMNS = [
    "id",
    "rule_id",
    "opportunity_type",
    "severity",
    "priority_score",
    "status",
    "title",
    "target",
    "remediation",
    "rule_version",
    "formula_version",
    "created_at",
]

_EXPORT_TITLE = "Searchify — Opportunities"


def rows_to_csv(items: list[dict]) -> str:
    """Render projected opportunity ``items`` as CSV (RFC-4180 via stdlib)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=OPPORTUNITIES_COLUMNS, extrasaction="ignore"
    )
    writer.writeheader()
    for item in items:
        row = {col: _csv_cell(item.get(col)) for col in OPPORTUNITIES_COLUMNS}
        writer.writerow(row)
    return buffer.getvalue()


def rows_to_markdown(items: list[dict]) -> str:
    """Render projected opportunity ``items`` as a Markdown table.

    Always emits the title + header + separator, so an empty result set is a
    valid (empty) table rather than a broken document.
    """
    columns = OPPORTUNITIES_COLUMNS
    lines = [f"# {_EXPORT_TITLE}", ""]
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("|" + "|".join(["---"] * len(columns)) + "|")
    for item in items:
        cells = " | ".join(_md_cell(item.get(col)) for col in columns)
        lines.append("| " + cells + " |")
    lines.append("")
    return "\n".join(lines)
