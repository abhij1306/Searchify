# Deterministic page-type classification (v2 P1 — spec §5.1).
#
# ``classify(final_url, facts)`` assigns every analyzed page a config-owned
# ``page_kind`` (homepage / article / product / category / pricing / docs /
# faq / about_contact / other) with a confidence score and bounded,
# explainable signal evidence. PURE: no I/O, no ORM, no LLM — the same
# inputs always yield the same type (invariant 9), and every pattern table,
# threshold, and weight is read from ``app.core.config.site_health``
# (invariant 1).
#
# Signal sources, evaluated in a FIXED priority order (spec §5.1):
#   1. root path            -> homepage (deterministic special case;
#                              HOMEPAGE_PATH_EQUIVALENTS covers locale roots /
#                              index variants; unlisted paths fall through)
#   2. URL path patterns    -> PAGE_KIND_PATH_PATTERNS, ordered, first match
#   3. content heuristics   -> question-heading ratio (faq) / price + cart
#                              markers (product) / byline + date (article)
#   4. structured-data types -> PAGE_KIND_SCHEMA_TYPE_MAP
#
# DELIBERATE SEMANTICS: signals 1-3 OUTRANK signal 4 on conflict. The schema
# markup is the page's *claim* about itself; letting the claim decide the
# type would make type-expected-schema rules circular. The winning signal is
# recorded as ``classified_by`` and the schema-suggested type as
# ``schema_suggested_type`` in the bounded evidence so the UI can explain
# the classification.
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.core.config import site_health as _config
from app.core.config.site_health_page_profiles import (
    CLASSIFICATION_MAX_ALTERNATIVES,
    CLASSIFICATION_OTHER_REASON_BELOW_THRESHOLD,
    CLASSIFICATION_OTHER_REASON_NO_SIGNALS,
)

# Bounded per-input caps so a hostile URL/body can never bloat the evidence
# or the classification work (same bounding convention as parser.py).
_MAX_PATH_CHARS = _config.SITE_HEALTH_MAX_PATH_CHARS
_MAX_SIGNAL_DETAIL_CHARS = _config.SITE_HEALTH_MAX_SIGNAL_DETAIL_CHARS
# Compiled once from the config tables (deterministic; the tables are frozen
# config, so compilation at import is exact).
_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (page_kind, re.compile(pattern))
    for page_kind, pattern in _config.PAGE_KIND_PATH_PATTERNS
)
_PRICE_RE = re.compile(_config.PAGE_KIND_PRICE_PATTERN, re.IGNORECASE)
_BYLINE_RE = re.compile(_config.PAGE_KIND_BYLINE_PATTERN)
_DATE_RE = re.compile(_config.PAGE_KIND_DATE_PATTERN, re.IGNORECASE)


@dataclass(frozen=True)
class PageKindAssessment:
    """The bounded, deterministic result of classifying one page.

    ``page_kind`` is a config ``PAGE_KINDS`` member (falling back to
    ``other``); ``confidence`` is the sum of the matched signal weights;
    ``signals`` is the bounded matched-signal evidence (at most one entry
    per signal source, priority order); ``classified_by`` is the winning
    signal name (``none`` when nothing matched);
    ``schema_suggested_type`` is what the structured-data signal alone would
    have suggested (None when no recognized mapping), recorded so a
    URL/content-vs-schema conflict is explainable in the UI.
    """

    page_kind: str
    confidence: float
    signals: tuple[dict[str, Any], ...]
    classifier_version: str
    classified_by: str
    schema_suggested_type: str | None
    alternatives: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    other_reason: str | None

    def to_evidence(self) -> dict[str, Any]:
        """Bounded, JSON-safe evidence dict persisted into the facts dict."""
        return {
            "classifier_version": self.classifier_version,
            "classified_by": self.classified_by,
            "schema_suggested_type": self.schema_suggested_type,
            "confidence": self.confidence,
            "confidence_threshold": _config.PAGE_KIND_CONFIDENCE_THRESHOLD,
            "signals": [dict(signal) for signal in self.signals],
            "alternatives": [dict(item) for item in self.alternatives],
            "conflicts": [dict(item) for item in self.conflicts],
            "other_reason": self.other_reason,
        }


def _normalized_path(final_url: str) -> str:
    """Lowercase path with trailing slashes stripped ("" for the root).

    Bounded and guarded: an unparseable URL yields "" (the root form), which
    is itself a deterministic classification input.
    """
    try:
        path = urlsplit(final_url or "").path or ""
    except Exception:
        return ""
    path = path[:_MAX_PATH_CHARS].lower()
    while path.endswith("/"):
        path = path[:-1]
    return path


def _is_absolute_http_url(final_url: str) -> bool:
    """Whether a URL is an absolute http(s) URL with a real host.

    Classification reasons about a page's PATH, which only means something
    once we know which document the path belongs to. Anything else contributes
    no signals rather than defaulting to the root.
    """
    try:
        parts = urlsplit(str(final_url or ""))
    # Same guard as ``_normalized_path``: the two call ``urlsplit`` on the same
    # input, so a narrower scope here would let a malformed URL raise out of one
    # while the other quietly returned a value for it.
    except Exception:  # noqa: BLE001
        return False
    # ``hostname``, not ``netloc``: ``http://user@/products`` carries a non-empty
    # netloc with no host at all, and deriving path signals from it would attach
    # findings to a URL that names no document.
    return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)


def _signal(signal: str, page_kind: str, detail: str) -> dict[str, Any]:
    """One bounded matched-signal record (weight from the config table)."""
    return {
        "signal": signal,
        "page_kind": page_kind,
        # ``.get`` with a 0.0 default: a signal constant added without a weight
        # should contribute nothing, not raise KeyError and fail the whole
        # classification of an otherwise analyzable page.
        "weight": float(_config.PAGE_KIND_SIGNAL_WEIGHTS.get(signal, 0.0)),
        "detail": detail[:_MAX_SIGNAL_DETAIL_CHARS],
    }


def is_question_heading(text: str) -> bool:
    """Question-form heading: ends with "?" or starts with a question word.

    Public since sh-extractor-2: the parser's ``question_heading_ratio`` fact
    and the FAQ content heuristic share this one definition.
    """
    normalized = " ".join(str(text or "").split()).lower()
    if not normalized:
        return False
    if normalized.endswith("?"):
        return True
    first_word = normalized.split(" ", 1)[0].strip("¿?¡!.,:;\"'")
    return first_word in _config.PAGE_KIND_QUESTION_WORDS


def _mapping(value: Any) -> dict[str, Any]:
    """A nested fact as a mapping, or ``{}`` when it is the wrong shape.

    The facts dict normally comes from our own extractor, but it is also read
    back from persisted JSON written by an older extractor version. A field
    that is not a mapping must contribute NO signals rather than raise: the
    classifier's contract is that partial facts simply match fewer signals.
    """
    return value if isinstance(value, dict) else {}


def _str_sequence(value: Any) -> list[str]:
    """A nested fact as a list of strings, or ``[]`` when wrongly shaped.

    A bare string is deliberately NOT treated as a one-item sequence: iterating
    it would yield characters and fabricate signals from nothing.
    """
    if isinstance(value, list | tuple):
        return [str(item) for item in value]
    return []


def _content_heuristic(facts: dict) -> dict[str, Any] | None:
    """Signal 3: the first matching content heuristic (faq -> product ->
    article), or None. Reads only bounded parser facts."""
    # FAQ: question-form heading ratio over the bounded h2 + h3 texts (spec
    # §5.1; h3 texts are extracted since sh-extractor-2 — absent h3s simply
    # contribute nothing, preserving P1 outcomes for h2-only pages).
    headings = _mapping(facts.get("headings"))
    heading_texts = _str_sequence(headings.get("h2_texts"))
    heading_texts += _str_sequence(headings.get("h3_texts"))
    if len(heading_texts) >= _config.PAGE_KIND_FAQ_MIN_HEADINGS:
        question_count = sum(1 for text in heading_texts if is_question_heading(text))
        ratio = question_count / len(heading_texts)
        if ratio >= _config.PAGE_KIND_FAQ_QUESTION_RATIO:
            return _signal(
                _config.PAGE_KIND_SIGNAL_CONTENT_HEURISTIC,
                _config.PAGE_KIND_FAQ,
                f"question_headings:{question_count}/{len(heading_texts)}",
            )

    body = _mapping(facts.get("body"))
    raw_body_text = body.get("text")
    body_text = raw_body_text if isinstance(raw_body_text, str) else ""

    # Product: a price token AND a cart marker in the bounded body text.
    if body_text and _PRICE_RE.search(body_text):
        lowered = body_text.lower()
        if any(marker in lowered for marker in _config.PAGE_KIND_CART_MARKERS):
            return _signal(
                _config.PAGE_KIND_SIGNAL_CONTENT_HEURISTIC,
                _config.PAGE_KIND_PRODUCT,
                "price_and_cart_markers",
            )

    # Article: author byline + date within a bounded prefix of the body.
    if body_text:
        prefix = body_text[: _config.PAGE_KIND_ARTICLE_SCAN_CHARS]
        if _BYLINE_RE.search(prefix) and _DATE_RE.search(prefix):
            return _signal(
                _config.PAGE_KIND_SIGNAL_CONTENT_HEURISTIC,
                _config.PAGE_KIND_ARTICLE,
                "byline_and_date",
            )
    return None


def _schema_suggestion(facts: dict) -> tuple[str | None, str | None]:
    """Signal 4: (suggested page_kind, matched schema type) or (None, None).

    Iterates the (sorted) structured-data type names so the first mapped
    type is deterministic.
    """
    structured = _mapping(facts.get("structured_data"))
    types = sorted(_str_sequence(structured.get("types")))
    for schema_type in types:
        page_kind = _config.PAGE_KIND_SCHEMA_TYPE_MAP.get(schema_type)
        if page_kind is not None:
            return page_kind, schema_type
    return None, None


def _alternatives(
    matched: list[dict[str, Any]], *, winner_type: str | None
) -> tuple[dict[str, Any], ...]:
    """Aggregate non-winning candidate types into bounded evidence.

    A candidate may have multiple supporting signals (for example a URL path
    and structured data).  Aggregating them makes the runner-up explainable
    without changing the priority-based winner policy.
    """
    candidates: dict[str, dict[str, Any]] = {}
    for signal in matched:
        page_kind = str(signal["page_kind"])
        if page_kind == winner_type:
            continue
        entry = candidates.setdefault(
            page_kind,
            {"page_kind": page_kind, "confidence": 0.0, "signals": []},
        )
        entry["confidence"] += float(signal["weight"])
        entry["signals"].append(str(signal["signal"]))
    ordered = sorted(
        candidates.values(), key=lambda item: (-item["confidence"], item["page_kind"])
    )[:CLASSIFICATION_MAX_ALTERNATIVES]
    return tuple(
        {
            "page_kind": item["page_kind"],
            "confidence": round(float(item["confidence"]), 4),
            "signals": item["signals"],
        }
        for item in ordered
    )


def _conflicts(
    matched: list[dict[str, Any]], *, winner_type: str | None
) -> tuple[dict[str, Any], ...]:
    """Record only material disagreements between classification signals."""
    if winner_type is None:
        return ()
    conflicts = [
        {
            "winner_page_kind": winner_type,
            "conflicting_page_kind": str(signal["page_kind"]),
            "signal": str(signal["signal"]),
            "detail": str(signal["detail"]),
        }
        for signal in matched
        if str(signal["page_kind"]) != winner_type
    ]
    return tuple(conflicts[:CLASSIFICATION_MAX_ALTERNATIVES])


def classify(final_url: str, facts: dict) -> PageKindAssessment:
    """Classify one page into the config taxonomy (pure, deterministic).

    Evaluates all four signal sources in the fixed priority order, takes the
    highest-priority matched signal as the winner, and sums the matched
    signal weights into ``confidence``. Below the config threshold the page
    falls back to ``other``. Never raises on malformed facts (partial facts
    simply match fewer signals).
    """
    matched, schema_page_kind = _classification_signals(final_url, _mapping(facts))
    confidence, winner, page_kind, other_reason = _classification_outcome(matched)
    winner_type = str(winner["page_kind"]) if winner is not None else None
    classified_by = (
        winner["signal"] if winner is not None else _config.PAGE_KIND_SIGNAL_NONE
    )
    return PageKindAssessment(
        page_kind=page_kind,
        confidence=confidence,
        signals=tuple(matched),
        classifier_version=_config.CLASSIFIER_VERSION,
        classified_by=classified_by,
        schema_suggested_type=schema_page_kind,
        alternatives=_alternatives(matched, winner_type=winner_type),
        conflicts=_conflicts(matched, winner_type=winner_type),
        other_reason=other_reason,
    )


def _classification_signals(
    final_url: str, facts: dict
) -> tuple[list[dict[str, Any]], str | None]:
    matched: list[dict[str, Any]] = []
    # A missing or malformed URL has no path to reason about. Falling through
    # to ``_normalized_path`` yields "" for all of them, which IS a homepage
    # equivalent — so ``classify("", {})`` and ``classify("http://", {})`` both
    # used to report a confident homepage for a page we never located.
    if not _is_absolute_http_url(final_url):
        return matched, None
    path = _normalized_path(final_url)

    # Signal 1 — root path → homepage (deterministic special case).
    if path in _config.HOMEPAGE_PATH_EQUIVALENTS:
        matched.append(
            _signal(
                _config.PAGE_KIND_SIGNAL_ROOT_PATH,
                _config.PAGE_KIND_HOMEPAGE,
                path or "/",
            )
        )

    # Signal 2 — ordered path patterns, first match wins.
    for page_kind, pattern in _PATH_PATTERNS:
        if pattern.match(path):
            matched.append(
                _signal(
                    _config.PAGE_KIND_SIGNAL_PATH_PATTERN,
                    page_kind,
                    pattern.pattern,
                )
            )
            break

    # Signal 3 — content/heading heuristics.
    heuristic = _content_heuristic(facts)
    if heuristic is not None:
        matched.append(heuristic)

    # Signal 4 — structured-data types (evaluated always, so the suggested
    # type is recorded in the evidence even when outranked).
    schema_page_kind, schema_type = _schema_suggestion(facts)
    if schema_page_kind is not None:
        matched.append(
            _signal(
                _config.PAGE_KIND_SIGNAL_STRUCTURED_DATA,
                schema_page_kind,
                schema_type or "",
            )
        )
    return matched, schema_page_kind


def _classification_outcome(
    matched: list[dict[str, Any]],
) -> tuple[float, dict[str, Any] | None, str, str | None]:
    # Fixed priority order: signals were appended in priority order already;
    # the winner is the first matched signal (signals 1-3 outrank 4).
    confidence = round(sum(signal["weight"] for signal in matched), 4)
    winner = matched[0] if matched else None
    below_threshold = confidence < _config.PAGE_KIND_CONFIDENCE_THRESHOLD
    page_kind = (
        winner["page_kind"]
        if winner is not None and not below_threshold
        else _config.PAGE_KIND_OTHER
    )
    other_reason = None
    if page_kind == _config.PAGE_KIND_OTHER:
        other_reason = (
            CLASSIFICATION_OTHER_REASON_NO_SIGNALS
            if winner is None
            else CLASSIFICATION_OTHER_REASON_BELOW_THRESHOLD
        )
    return confidence, winner, page_kind, other_reason
