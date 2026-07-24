# Deterministic page-type classification (v2 P1 — spec §5.1).
#
# ``classify(final_url, facts)`` assigns every analyzed page a config-owned
# ``page_type`` (homepage / article / product / category / pricing / docs /
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
#   2. URL path patterns    -> PAGE_TYPE_PATH_PATTERNS, ordered, first match
#   3. content heuristics   -> question-heading ratio (faq) / price + cart
#                              markers (product) / byline + date (article)
#   4. structured-data types -> PAGE_TYPE_SCHEMA_TYPE_MAP
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

# Bounded per-input caps so a hostile URL/body can never bloat the evidence
# or the classification work (same bounding convention as parser.py).
_MAX_PATH_CHARS = 512
_MAX_SIGNAL_DETAIL_CHARS = 256

# Compiled once from the config tables (deterministic; the tables are frozen
# config, so compilation at import is exact).
_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (page_type, re.compile(pattern))
    for page_type, pattern in _config.PAGE_TYPE_PATH_PATTERNS
)
_PRICE_RE = re.compile(_config.PAGE_TYPE_PRICE_PATTERN, re.IGNORECASE)
_BYLINE_RE = re.compile(_config.PAGE_TYPE_BYLINE_PATTERN)
_DATE_RE = re.compile(_config.PAGE_TYPE_DATE_PATTERN, re.IGNORECASE)


@dataclass(frozen=True)
class PageTypeAssessment:
    """The bounded, deterministic result of classifying one page.

    ``page_type`` is a config ``PAGE_TYPES`` member (falling back to
    ``other``); ``confidence`` is the sum of the matched signal weights;
    ``signals`` is the bounded matched-signal evidence (at most one entry
    per signal source, priority order); ``classified_by`` is the winning
    signal name (``none`` when nothing matched);
    ``schema_suggested_type`` is what the structured-data signal alone would
    have suggested (None when no recognized mapping), recorded so a
    URL/content-vs-schema conflict is explainable in the UI.
    """

    page_type: str
    confidence: float
    signals: tuple[dict[str, Any], ...]
    classifier_version: str
    classified_by: str
    schema_suggested_type: str | None

    def to_evidence(self) -> dict[str, Any]:
        """Bounded, JSON-safe evidence dict persisted into the facts dict."""
        return {
            "classifier_version": self.classifier_version,
            "classified_by": self.classified_by,
            "schema_suggested_type": self.schema_suggested_type,
            "confidence": self.confidence,
            "confidence_threshold": _config.PAGE_TYPE_CONFIDENCE_THRESHOLD,
            "signals": [dict(signal) for signal in self.signals],
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


def _signal(signal: str, page_type: str, detail: str) -> dict[str, Any]:
    """One bounded matched-signal record (weight from the config table)."""
    return {
        "signal": signal,
        "page_type": page_type,
        "weight": float(_config.PAGE_TYPE_SIGNAL_WEIGHTS[signal]),
        "detail": detail[:_MAX_SIGNAL_DETAIL_CHARS],
    }


def _is_question_heading(text: str) -> bool:
    """Question-form heading: ends with "?" or starts with a question word."""
    normalized = " ".join(str(text or "").split()).lower()
    if not normalized:
        return False
    if normalized.endswith("?"):
        return True
    first_word = normalized.split(" ", 1)[0].strip("¿?¡!.,:;\"'")
    return first_word in _config.PAGE_TYPE_QUESTION_WORDS


def _content_heuristic(facts: dict) -> dict[str, Any] | None:
    """Signal 3: the first matching content heuristic (faq -> product ->
    article), or None. Reads only bounded parser facts."""
    # FAQ: question-form heading ratio over the bounded h2 texts (h3 texts
    # arrive with the P2 extractor — sh-extractor stays v1 in P1).
    headings = facts.get("headings") or {}
    h2_texts = [str(t) for t in (headings.get("h2_texts") or [])]
    if len(h2_texts) >= _config.PAGE_TYPE_FAQ_MIN_HEADINGS:
        question_count = sum(1 for text in h2_texts if _is_question_heading(text))
        ratio = question_count / len(h2_texts)
        if ratio >= _config.PAGE_TYPE_FAQ_QUESTION_RATIO:
            return _signal(
                _config.PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC,
                _config.PAGE_TYPE_FAQ,
                f"question_headings:{question_count}/{len(h2_texts)}",
            )

    body = facts.get("body") or {}
    body_text = str(body.get("text") or "")

    # Product: a price token AND a cart marker in the bounded body text.
    if body_text and _PRICE_RE.search(body_text):
        lowered = body_text.lower()
        if any(
            marker in lowered for marker in _config.PAGE_TYPE_CART_MARKERS
        ):
            return _signal(
                _config.PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC,
                _config.PAGE_TYPE_PRODUCT,
                "price_and_cart_markers",
            )

    # Article: author byline + date within a bounded prefix of the body.
    if body_text:
        prefix = body_text[: _config.PAGE_TYPE_ARTICLE_SCAN_CHARS]
        if _BYLINE_RE.search(prefix) and _DATE_RE.search(prefix):
            return _signal(
                _config.PAGE_TYPE_SIGNAL_CONTENT_HEURISTIC,
                _config.PAGE_TYPE_ARTICLE,
                "byline_and_date",
            )
    return None


def _schema_suggestion(facts: dict) -> tuple[str | None, str | None]:
    """Signal 4: (suggested page_type, matched schema type) or (None, None).

    Iterates the (sorted) structured-data type names so the first mapped
    type is deterministic.
    """
    structured = facts.get("structured_data") or {}
    types = sorted(str(t) for t in (structured.get("types") or []))
    for schema_type in types:
        page_type = _config.PAGE_TYPE_SCHEMA_TYPE_MAP.get(schema_type)
        if page_type is not None:
            return page_type, schema_type
    return None, None


def classify(final_url: str, facts: dict) -> PageTypeAssessment:
    """Classify one page into the config taxonomy (pure, deterministic).

    Evaluates all four signal sources in the fixed priority order, takes the
    highest-priority matched signal as the winner, and sums the matched
    signal weights into ``confidence``. Below the config threshold the page
    falls back to ``other``. Never raises on malformed facts (partial facts
    simply match fewer signals).
    """
    facts = facts or {}
    path = _normalized_path(final_url)
    matched: list[dict[str, Any]] = []

    # Signal 1 — root path → homepage (deterministic special case).
    if path in _config.HOMEPAGE_PATH_EQUIVALENTS:
        matched.append(
            _signal(
                _config.PAGE_TYPE_SIGNAL_ROOT_PATH,
                _config.PAGE_TYPE_HOMEPAGE,
                path or "/",
            )
        )

    # Signal 2 — ordered path patterns, first match wins.
    for page_type, pattern in _PATH_PATTERNS:
        if pattern.match(path):
            matched.append(
                _signal(
                    _config.PAGE_TYPE_SIGNAL_PATH_PATTERN,
                    page_type,
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
    schema_page_type, schema_type = _schema_suggestion(facts)
    if schema_page_type is not None:
        matched.append(
            _signal(
                _config.PAGE_TYPE_SIGNAL_STRUCTURED_DATA,
                schema_page_type,
                schema_type or "",
            )
        )

    # Fixed priority order: signals were appended in priority order already;
    # the winner is the first matched signal (signals 1-3 outrank 4).
    confidence = round(sum(signal["weight"] for signal in matched), 4)
    winner = matched[0] if matched else None
    below_threshold = confidence < _config.PAGE_TYPE_CONFIDENCE_THRESHOLD
    page_type = (
        winner["page_type"] if winner is not None and not below_threshold
        else _config.PAGE_TYPE_OTHER
    )
    classified_by = (
        winner["signal"] if winner is not None else _config.PAGE_TYPE_SIGNAL_NONE
    )
    return PageTypeAssessment(
        page_type=page_type,
        confidence=confidence,
        signals=tuple(matched),
        classifier_version=_config.CLASSIFIER_VERSION,
        classified_by=classified_by,
        schema_suggested_type=schema_page_type,
    )
