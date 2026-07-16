# Deterministic input normalization for projects + prompts (ported B3).
#
# Adapts the reference ``_normalize_prompts`` / ``_normalize_benchmark_mode``
# (crawlerai ``ai_visibility/service.py``) to Searchify. Validation of the
# enum *values* happens in the Pydantic schemas; these helpers normalize the
# free-text fields (trim, casefold intent, drop unknown intents) so what lands
# in the database is canonical regardless of how it was entered.
from __future__ import annotations

from typing import Any

from app.core.config.projects import (
    BENCHMARK_MODES,
    DEFAULT_BENCHMARK_MODE,
    PROMPT_INTENTS,
)


def normalize_intent(value: Any) -> str:
    """Casefold + trim an intent; drop it if it is not a known intent.

    An empty / unknown intent normalizes to ``""`` ("unspecified"), matching
    the reference behaviour.
    """
    intent = str(value or "").strip().lower()
    if intent and intent not in PROMPT_INTENTS:
        return ""
    return intent


def normalize_benchmark_mode(value: Any) -> str:
    """Trim + casefold a benchmark mode; empty -> default; unknown -> error."""
    mode = str(value or "").strip().lower()
    if not mode:
        return DEFAULT_BENCHMARK_MODE
    if mode not in BENCHMARK_MODES:
        raise ValueError(f"Unsupported benchmark_mode: {mode}")
    return mode


def normalize_prompt_rows(
    prompts: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize a list of raw prompt dicts (text/theme/intent...).

    Drops rows with empty text, trims text/theme, casefolds+validates intent,
    and preserves the ``branded``/``enabled``/``origin``/``generation_evidence``
    fields when present. Mirrors the reference ``_normalize_prompts`` while
    carrying the extra columns Searchify's dedicated prompt resource adds.
    """
    normalized: list[dict[str, Any]] = []
    for prompt in prompts or []:
        text = str(prompt.get("text") or "").strip()
        if not text:
            continue
        row: dict[str, Any] = {
            "text": text,
            "theme": str(prompt.get("theme") or "").strip(),
            "intent": normalize_intent(prompt.get("intent")),
        }
        if "branded" in prompt:
            row["branded"] = bool(prompt.get("branded"))
        if "enabled" in prompt:
            row["enabled"] = bool(prompt.get("enabled"))
        if prompt.get("origin"):
            row["origin"] = str(prompt.get("origin"))
        if prompt.get("generation_evidence") is not None:
            row["generation_evidence"] = prompt.get("generation_evidence")
        normalized.append(row)
    return normalized
