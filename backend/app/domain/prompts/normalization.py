# Prompt-text normalization for dedupe (one owner, invariant 2).
#
# The ``(prompt_set_id, normalized_text_hash)`` uniqueness on ``prompts`` makes
# duplicate handling conflict-safe at the DB layer; every code path that writes
# prompt text (manual create, CSV import, AI generation, edits) computes the
# hash through this module so "same concept" means the same thing everywhere.
from __future__ import annotations

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")
# Trailing punctuation that doesn't change the concept ("best shoes?" == "best shoes").
_TRAILING_PUNCTUATION = re.compile(r"[\s?.!,;:]+$")


def normalize_prompt_text(text: str) -> str:
    """Casefold, collapse whitespace, and strip trailing punctuation."""
    collapsed = _WHITESPACE.sub(" ", text).strip().casefold()
    return _TRAILING_PUNCTUATION.sub("", collapsed)


def prompt_text_hash(text: str) -> str:
    """sha256 hex digest of the normalized text — the dedupe key."""
    return hashlib.sha256(normalize_prompt_text(text).encode("utf-8")).hexdigest()
