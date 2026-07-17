# Structured-data (JSON-LD / microdata) parse + validation (Task 5).
#
# Pure, deterministic, hardened helpers that turn the raw structured-data blocks
# an HTML page carries into bounded, normalized fact dicts and validate each
# recognized schema.org type against the config-owned
# ``STRUCTURED_DATA_REQUIRED_PROPERTIES`` map. No I/O, no ORM.
#
# Hardening: JSON-LD is parsed with the stdlib ``json`` loader (no XML external
# entity surface); microdata is walked over an already-parsed lxml tree. Any
# malformed block is skipped (never raises) so a hostile page yields partial
# facts, never a crash (subplan Persistence contract).
from __future__ import annotations

import json
from typing import Any

from app.core.config.site_health import STRUCTURED_DATA_REQUIRED_PROPERTIES

# Absolute ceiling on how deep we descend into a JSON-LD object graph so a
# deeply-nested (or self-referential) payload can never blow the stack.
_MAX_JSONLD_DEPTH = 12


def _clean_type(value: Any) -> str:
    """Normalize a schema.org ``@type`` token to its bare type name.

    Accepts ``"Article"``, ``"http://schema.org/Article"``, or a list whose
    first entry is a type; returns ``""`` for anything unrecognized.
    """
    if isinstance(value, list):
        for item in value:
            cleaned = _clean_type(item)
            if cleaned:
                return cleaned
        return ""
    if not isinstance(value, str):
        return ""
    token = value.strip()
    if not token:
        return ""
    # Strip a schema.org URL prefix / trailing slash so "schema.org/Article"
    # and "Article" collapse to the same recognized type.
    token = token.rstrip("/")
    if "/" in token:
        token = token.rsplit("/", 1)[-1]
    if "#" in token:
        token = token.rsplit("#", 1)[-1]
    return token


def _iter_jsonld_objects(node: Any, depth: int = 0):
    """Yield every dict node in a JSON-LD payload (bounded recursion).

    Descends into ``@graph`` arrays and nested lists/dicts up to
    ``_MAX_JSONLD_DEPTH`` so a nested Organization inside a WebPage is still
    discovered, while a pathological payload can never recurse without bound.
    """
    if depth > _MAX_JSONLD_DEPTH:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _iter_jsonld_objects(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                yield from _iter_jsonld_objects(item, depth + 1)


def _present_properties(obj: dict, required: tuple[str, ...]) -> list[str]:
    """Return the required properties actually present + non-empty on ``obj``."""
    present: list[str] = []
    for prop in required:
        if prop in obj and obj[prop] not in (None, "", [], {}):
            present.append(prop)
    return present


def _validate_object(obj: dict) -> dict | None:
    """Validate one JSON-LD object against the required-property map.

    Returns a bounded fact dict (``type`` + required/present/missing property
    lists + a ``valid`` flag) for a RECOGNIZED type, or ``None`` when the object
    carries no recognized ``@type`` (so callers only record understood types).
    """
    schema_type = _clean_type(obj.get("@type"))
    if not schema_type or schema_type not in STRUCTURED_DATA_REQUIRED_PROPERTIES:
        return None
    required = STRUCTURED_DATA_REQUIRED_PROPERTIES[schema_type]
    present = _present_properties(obj, required)
    missing = [prop for prop in required if prop not in present]
    return {
        "type": schema_type,
        "syntax": "json-ld",
        "required": list(required),
        "present": present,
        "missing": missing,
        "valid": not missing,
    }


def parse_jsonld_blocks(raw_blocks: list[str], *, max_blocks: int) -> list[dict]:
    """Parse + validate a bounded list of raw JSON-LD script bodies.

    Each element of ``raw_blocks`` is the text of one
    ``<script type="application/ld+json">``. Malformed JSON is skipped. Returns
    one bounded fact dict per recognized schema.org object across all blocks,
    capped at ``max_blocks`` (invariant 9: deterministic + bounded).
    """
    facts: list[dict] = []
    for body in raw_blocks:
        if len(facts) >= max_blocks:
            break
        text = (body or "").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError, RecursionError):
            # Malformed/pathologically-nested JSON-LD: skip this block, keep
            # the rest (partial facts). A deeply nested block can raise
            # RecursionError inside json.loads() itself, before
            # _iter_jsonld_objects()'s own depth cap ever runs.
            continue
        for obj in _iter_jsonld_objects(parsed):
            if len(facts) >= max_blocks:
                break
            fact = _validate_object(obj)
            if fact is not None:
                facts.append(fact)
    return facts


def validate_microdata_types(itemtypes: list[str], *, max_blocks: int) -> list[dict]:
    """Turn bounded microdata ``itemtype`` URLs into recognized-type facts.

    Microdata property extraction is intentionally shallow (the AEO rules only
    need "is a recognized schema.org type present"); we record each recognized
    ``itemtype`` as a fact with an empty present/missing breakdown (properties
    are attribute-scattered in microdata and not required for the current
    rule set). Bounded by ``max_blocks``.
    """
    facts: list[dict] = []
    for raw in itemtypes:
        if len(facts) >= max_blocks:
            break
        # A valid `itemtype` attribute may list multiple space-separated
        # schema.org URLs; passing the whole attribute to `_clean_type()`
        # would discard every recognized type in a multi-value attribute.
        for candidate in str(raw or "").split():
            if len(facts) >= max_blocks:
                break
            schema_type = _clean_type(candidate)
            if (
                not schema_type
                or schema_type not in STRUCTURED_DATA_REQUIRED_PROPERTIES
            ):
                continue
            required = STRUCTURED_DATA_REQUIRED_PROPERTIES[schema_type]
            facts.append(
                {
                    "type": schema_type,
                    "syntax": "microdata",
                    "required": list(required),
                    "present": [],
                    "missing": list(required),
                    "valid": False,
                }
            )
    return facts
