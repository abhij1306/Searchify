# Opportunities persisted-data exports (workspace-safe).
#
# Pure renderers over ALREADY-PROJECTED dicts (the exact shape the service
# layer produces for the opportunities list). They never re-score, never
# fetch, and never read a raw body — the router hands them the same
# workspace-scoped projection the JSON API returns, so an export can never
# leak more than the API. Mirrors ``analysis/site_health/exports.py``: CSV
# quoting/escaping is delegated to the stdlib ``csv`` writer; a cell beginning
# with a spreadsheet-formula trigger is prefixed with ``'`` to neutralize
# CSV/formula injection; Markdown cell content is escaped (and
# formula-neutralized) so a title containing ``|`` or a newline can never
# break the table. Empty result sets still render a valid header row.
from __future__ import annotations

import csv
import io
from typing import Any

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


def _cell(value: Any) -> str:
    """Stringify a value for a CSV/MD cell (``None``/absent -> empty string)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# Leading characters a spreadsheet treats as the start of a formula. A cell
# that begins with one is dangerous (CSV/formula injection), so it is prefixed
# with a single quote to neutralize evaluation while preserving the visible
# text (the widely-used OWASP mitigation). Whitespace/control characters
# (space, tab, CR, LF) hidden before a trigger do not stop a spreadsheet from
# evaluating the formula, so they are stripped before the check — and treated
# as dangerous themselves if leading, since tab/CR/LF can also delimit a
# formula for some importers.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")
_LEADING_WHITESPACE = " \t\r\n\x0b\x0c"


def _has_formula_trigger(text: str) -> bool:
    """True if ``text``, after stripping leading whitespace, starts a formula.

    Inspects the first non-whitespace character (not just ``text[0]``) so a
    value like ``"\\t=HYPERLINK(...)"`` or a newline-prefixed formula is still
    caught; CSV quoting alone does not stop a spreadsheet from evaluating it.
    """
    if not text:
        return False
    if text[0] in ("\t", "\r", "\n"):
        return True
    stripped = text.lstrip(_LEADING_WHITESPACE)
    return bool(stripped) and stripped[0] in _FORMULA_TRIGGERS


def _csv_cell(value: Any) -> str:
    """CSV cell with spreadsheet-formula neutralization (see site-health)."""
    text = _cell(value)
    if _has_formula_trigger(text):
        return "'" + text
    return text


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


def _md_cell(value: Any) -> str:
    """Escape a value so it is a single safe Markdown table cell."""
    text = _cell(value)
    if _has_formula_trigger(text):
        # Neutralize a leading formula trigger for the same reason as CSV: the
        # exported table may be pasted into a spreadsheet.
        text = "'" + text
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", " ")
    )


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
