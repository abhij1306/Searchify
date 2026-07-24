# Shared CSV/MD cell stringify + spreadsheet formula-injection neutralization
# (invariant 2: single owner — site-health and product exports import this;
# do not copy it into another exporter).
#
# A cell whose first non-whitespace character is ``=``, ``+``, ``-``, or ``@``
# (or that begins with a tab/CR/LF) is dangerous when opened in a spreadsheet
# (CSV/formula injection), so it is prefixed with a single quote to neutralize
# evaluation while preserving the visible text (the widely-used OWASP
# mitigation). Whitespace/control characters (space, tab, CR, LF) hidden
# before a trigger do not stop a spreadsheet from evaluating the formula, so
# they are stripped before the check — and treated as dangerous themselves if
# leading, since tab/CR/LF can also delimit a formula for some importers.
from __future__ import annotations

from typing import Any

_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")
_LEADING_WHITESPACE = " \t\r\n\x0b\x0c"


def stringify_cell(value: Any) -> str:
    """Stringify a value for a CSV/MD cell (``None``/absent -> empty string)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def has_formula_trigger(text: str) -> bool:
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


def csv_cell(value: Any) -> str:
    """CSV cell with spreadsheet-formula neutralization.

    The stdlib ``csv`` writer still handles quoting/escaping of commas,
    quotes, and newlines on top of this.
    """
    text = stringify_cell(value)
    if has_formula_trigger(text):
        return "'" + text
    return text
