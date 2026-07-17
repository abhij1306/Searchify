# CSV parsing for MVP prompt bulk-import.
#
# The import endpoint accepts either already-parsed JSON rows OR a raw CSV
# upload (the committed frontend contract posts a CSV ``File``; a future F7 may
# parse in the browser and post rows instead). This helper turns raw CSV text
# into ``PromptInput`` rows so both paths converge on the same create logic.
from __future__ import annotations

import csv
import io

from app.domain.prompts.schemas import PromptInput

# Accepted header aliases -> canonical field. Case/space-insensitive.
_TEXT_KEYS = {"text", "prompt", "query", "question"}
_THEME_KEYS = {"theme", "topic", "category"}
_INTENT_KEYS = {"intent"}
_BRANDED_KEYS = {"branded", "is_branded"}
_ENABLED_KEYS = {"enabled", "is_enabled", "active"}

_TRUTHY = {"1", "true", "yes", "y", "t"}


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    token = value.strip().lower()
    if not token:
        return default
    return token in _TRUTHY


def parse_prompt_csv(content: str) -> list[PromptInput]:
    """Parse CSV text into ``PromptInput`` rows.

    Supports a header row (``text,theme,intent,branded,enabled`` in any order,
    with common aliases) or a headerless single-column file of prompt texts.
    Empty rows are skipped; unknown intents are normalized to ``""`` downstream.
    """
    text = content.lstrip("\ufeff")
    if not text.strip():
        return []
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    has_header = any(cell in _TEXT_KEYS for cell in header)
    if not has_header:
        # Headerless: treat the first column of each row as the prompt text.
        return [
            PromptInput(text=row[0].strip()) for row in rows if row and row[0].strip()
        ]

    def _col(keys: set[str]) -> int | None:
        for index, name in enumerate(header):
            if name in keys:
                return index
        return None

    text_i = _col(_TEXT_KEYS)
    theme_i = _col(_THEME_KEYS)
    intent_i = _col(_INTENT_KEYS)
    branded_i = _col(_BRANDED_KEYS)
    enabled_i = _col(_ENABLED_KEYS)

    def _cell(row: list[str], index: int | None) -> str | None:
        if index is None or index >= len(row):
            return None
        return row[index]

    prompts: list[PromptInput] = []
    for row in rows[1:]:
        raw_text = (_cell(row, text_i) or "").strip()
        if not raw_text:
            continue
        prompts.append(
            PromptInput(
                text=raw_text,
                theme=(_cell(row, theme_i) or "").strip(),
                intent=(_cell(row, intent_i) or "").strip(),
                branded=_as_bool(_cell(row, branded_i), default=False),
                enabled=_as_bool(_cell(row, enabled_i), default=True),
            )
        )
    return prompts
