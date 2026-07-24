"""Deterministic product-visibility scoring (no LLM — invariant 9).

Sibling analyzer pass to ``analysis/scoring.py`` (brand level): scores product
mentions, rank-in-list, and price accuracy over the same persisted answer
text. Pure functions only — no I/O, no ORM, no provider. Every knob comes
from ``app/core/config/products.py`` (invariant 1); matching reuses
``analysis/normalization.py`` (imported, not copied — invariant 2).

Matching semantics: each catalog entry's match-alias set is name + SKU +
aliases + variant names/SKUs (folded by ``from_project``). Alias containment
runs on the normalized text (``normalize_alias`` — SKU punctuation survives
as tokens); rank/price extraction runs on the ORIGINAL answer text (list
structure and ``$`` markers are destroyed by normalization), located via a
token-tolerant regex for the same aliases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.analysis.normalization import (
    first_alias_offset,
    normalize_alias,
)
from app.core.config.products import (
    PRICE_CURRENCY_PATTERNS,
    PRODUCT_PRICE_TOLERANCE_ABS,
    PRODUCT_PRICE_TOLERANCE_PCT,
    PRODUCT_PRICE_WINDOW_CHARS,
    PRODUCT_RANK_BUCKET_UNRANKED,
    PRODUCT_RANK_BUCKETS,
)


# --------------------------------------------------------------------------
# Config entries
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ProductEntry:
    id: str
    sku: str
    name: str
    # Full match-alias set (name + sku + aliases + variants), folded at
    # config-build time.
    aliases: tuple[str, ...]
    price: float | None
    currency: str


@dataclass(frozen=True)
class CompetitorProductEntry:
    id: str
    competitor: str
    name: str
    aliases: tuple[str, ...]
    price: float | None
    currency: str


def _as_price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _match_aliases(*parts: Any) -> tuple[str, ...]:
    """Fold name/sku/aliases/variant tokens into one deduped match-alias set."""
    seen: set[str] = set()
    aliases: list[str] = []
    for part in parts:
        for value in part if isinstance(part, (list, tuple)) else [part]:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.add(text)
                aliases.append(text)
    return tuple(aliases)


@dataclass(frozen=True)
class ProductScoringConfig:
    products: tuple[ProductEntry, ...] = field(default_factory=tuple)
    competitor_products: tuple[CompetitorProductEntry, ...] = field(
        default_factory=tuple
    )
    price_tolerance_pct: float = PRODUCT_PRICE_TOLERANCE_PCT
    price_tolerance_abs: float = PRODUCT_PRICE_TOLERANCE_ABS

    @classmethod
    def from_project(cls, config: dict[str, Any]) -> ProductScoringConfig:
        """Build from the audit's FROZEN catalog dict (never live config).

        Reads the ``products`` / ``competitor_products`` keys the planner
        froze via ``project_product_identity`` (mirrors
        ``ScoringConfig.from_project``).
        """
        products = []
        for item in config.get("products") or []:
            variants = [v for v in (item.get("variants") or []) if isinstance(v, dict)]
            products.append(
                ProductEntry(
                    id=str(item.get("id") or ""),
                    sku=str(item.get("sku") or ""),
                    name=str(item.get("name") or ""),
                    aliases=_match_aliases(
                        item.get("name"),
                        item.get("sku"),
                        item.get("aliases") or [],
                        [v.get("name") for v in variants],
                        [v.get("sku") for v in variants],
                    ),
                    price=_as_price(item.get("price")),
                    currency=str(item.get("currency") or "").strip().upper(),
                )
            )
        competitor_products = []
        for item in config.get("competitor_products") or []:
            competitor_products.append(
                CompetitorProductEntry(
                    id=str(item.get("id") or ""),
                    competitor=str(item.get("competitor_name") or ""),
                    name=str(item.get("name") or ""),
                    aliases=_match_aliases(item.get("name"), item.get("aliases") or []),
                    price=_as_price(item.get("price")),
                    currency=str(item.get("currency") or "").strip().upper(),
                )
            )
        return cls(
            products=tuple(products),
            competitor_products=tuple(competitor_products),
        )


# --------------------------------------------------------------------------
# Alias matching (normalized haystack — mirrors the brand scorer)
# --------------------------------------------------------------------------
def _first_offset(aliases: tuple[str, ...], normalized_haystack: str) -> int | None:
    offsets = [
        offset
        for alias in aliases
        if (offset := first_alias_offset(normalize_alias(alias), normalized_haystack))
        is not None
    ]
    return min(offsets) if offsets else None


def _original_text_offset(aliases: tuple[str, ...], text: str) -> int | None:
    """Locate the earliest alias occurrence in the ORIGINAL text.

    Normalized offsets do not map back to the original string (normalization
    collapses whitespace/punctuation), so rank/price extraction re-locates the
    mention with a token-tolerant regex: alias tokens joined by arbitrary
    non-word runs, case-insensitive. Returns None when no alias maps back
    (e.g. NFKC-folded characters) — rank/price then stay absent, keeping the
    pass deterministic.
    """
    starts: list[int] = []
    for alias in aliases:
        tokens = normalize_alias(alias).split()
        if not tokens:
            continue
        pattern = r"(?<!\w)" + r"[^\w]+".join(re.escape(t) for t in tokens) + r"(?!\w)"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            starts.append(match.start())
    return min(starts) if starts else None


# --------------------------------------------------------------------------
# Price extraction (config-driven currency patterns)
# --------------------------------------------------------------------------
_NUMBER = r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)"


def _compiled_currency_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for currency, markers in PRICE_CURRENCY_PATTERNS.items():
        escaped = sorted((re.escape(m) for m in markers), key=len, reverse=True)
        # Symbol/code BEFORE the amount ("$2,499.00", "USD 49.99").
        prefix = re.compile(
            r"(?<![\w$€£])(?:" + "|".join(escaped) + r")\s?" + _NUMBER,
            flags=re.IGNORECASE,
        )
        patterns.append((currency, prefix))
        # ISO code AFTER the amount ("2,499.00 USD"). The widened lookbehind
        # keeps comma-decimal fragments ("1.149,00 EUR") from matching.
        suffix = re.compile(
            r"(?<![\w.,\d])" + _NUMBER + r"\s?(?:" + re.escape(currency) + r")\b",
            flags=re.IGNORECASE,
        )
        patterns.append((currency, suffix))
    return tuple(patterns)


_CURRENCY_PATTERNS = _compiled_currency_patterns()


def _to_amount(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def extract_price_mentions(
    text: str, offset: int, window: int = PRODUCT_PRICE_WINDOW_CHARS
) -> list[dict[str, Any]]:
    """Extract price mentions in a character window around ``offset``.

    Config-driven (``PRICE_CURRENCY_PATTERNS``): a number only counts as a
    price when a known currency marker is present, so every mention carries a
    resolved ISO currency. Overlapping matches (e.g. prefix vs suffix forms)
    are de-duped keeping the earliest/longest. Results are position-ordered;
    each item: ``{"text", "value", "currency", "offset"}`` with ``offset`` in
    original-text coordinates.
    """
    if not text:
        return []
    start = max(0, offset - window // 2)
    end = min(len(text), offset + window // 2)
    # Clip to the line containing the mention: a list item's price sits on
    # the same line, and a wider window would misattribute a neighbouring
    # item's price.
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end == -1:
        line_end = len(text)
    start = max(start, line_start)
    end = min(end, line_end)
    segment = text[start:end]

    matches: list[dict[str, Any]] = []
    for currency, pattern in _CURRENCY_PATTERNS:
        for match in pattern.finditer(segment):
            value = _to_amount(match.group("amount"))
            if value is None:
                continue
            matches.append(
                {
                    "text": match.group(0),
                    "value": value,
                    "currency": currency,
                    "offset": start + match.start(),
                    "_end": start + match.end(),
                }
            )
    # Earliest first, longest match wins ties; drop overlapping duplicates.
    matches.sort(key=lambda m: (m["offset"], -(m["_end"] - m["offset"])))
    accepted: list[dict[str, Any]] = []
    for match in matches:
        if any(
            match["offset"] < kept["_end"] and kept["offset"] < match["_end"]
            for kept in accepted
        ):
            continue
        accepted.append(match)
    for match in accepted:
        del match["_end"]
    return accepted


def price_matches_catalog(
    mentioned_value: float,
    mentioned_currency: str,
    entry: ProductEntry | CompetitorProductEntry,
    *,
    tolerance_pct: float = PRODUCT_PRICE_TOLERANCE_PCT,
    tolerance_abs: float = PRODUCT_PRICE_TOLERANCE_ABS,
) -> bool | None:
    """Whether a mentioned price matches the catalog price within tolerance.

    Returns None (not verifiable) when the catalog has no price or the
    currencies conflict; else compares within
    ``max(catalog * pct, abs floor)``.
    """
    if entry.price is None:
        return None
    if (
        mentioned_currency
        and entry.currency
        and mentioned_currency.strip().upper() != entry.currency.strip().upper()
    ):
        return None
    tolerance = max(entry.price * tolerance_pct, tolerance_abs)
    return abs(mentioned_value - entry.price) <= tolerance + 1e-9


# --------------------------------------------------------------------------
# Rank-in-list detection (enumerated blocks: numbered, bullets, tables)
# --------------------------------------------------------------------------
_NUMBERED_RE = re.compile(r"^\s*(\d{1,3})[.)]\s+\S")
_BULLET_RE = re.compile(r"^\s*[-*•]\s+\S")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _line_spans(text: str) -> list[tuple[int, str]]:
    """(absolute start offset, line) pairs covering ``text``."""
    spans: list[tuple[int, str]] = []
    position = 0
    for line in text.split("\n"):
        spans.append((position, line))
        position += len(line) + 1
    return spans


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return any(cells) and all(
        _TABLE_SEPARATOR_CELL_RE.match(cell) for cell in cells if cell
    )


def _rank_in_block(
    block: list[tuple[int, str]], match_offset: int, family: str
) -> int | None:
    """1-based ordinal of the block item whose span contains the offset."""
    if family == "table":
        # Skip header + separator rows; data rows enumerate from 1.
        data_rows = [row for row in block if not _is_table_separator(row[1])][1:]
        for ordinal, (start, line) in enumerate(data_rows, start=1):
            end = start + len(line)
            if start <= match_offset < end:
                return ordinal
        return None

    ordinal = 0
    item_start: int | None = None
    for index, (start, line) in enumerate(block):
        is_marker = (
            _NUMBERED_RE.match(line) if family == "numbered" else _BULLET_RE.match(line)
        )
        if is_marker:
            ordinal += 1
            item_start = start
        # An item's span runs to the next marker (or the block end).
        next_start = (
            block[index + 1][0] if index + 1 < len(block) else None
        )
        if item_start is not None and match_offset >= item_start:
            if next_start is None or match_offset < next_start:
                return ordinal
    return None


def detect_product_rank(answer_text: str, match_offset: int) -> int | None:
    """1-based rank of the enumerated item containing ``match_offset``.

    Parses contiguous enumerated blocks — ``1.``/``1)`` numbered lines,
    ``-``/``*``/``•`` bullets, and markdown table rows — and returns the
    ordinal of the item containing the offset. Returns None when the mention
    is not part of an enumeration (prose, headings, ...).
    """
    if not answer_text or match_offset < 0:
        return None
    lines = _line_spans(answer_text)

    # Group lines into contiguous same-family blocks. A numbered block
    # restarts when the explicit number does not increase (a new list).
    blocks: list[tuple[str, list[tuple[int, str]]]] = []
    current_family = ""
    current: list[tuple[int, str]] = []
    last_number = 0
    for start, line in lines:
        numbered = _NUMBERED_RE.match(line)
        family = ""
        if _TABLE_ROW_RE.match(line):
            family = "table"
        elif numbered:
            family = "numbered"
            if current_family == "numbered" and int(numbered.group(1)) <= last_number:
                family = "numbered_restart"
            last_number = int(numbered.group(1))
        elif _BULLET_RE.match(line):
            family = "bullet"
        elif line.strip() and current and current_family in {"numbered", "bullet"}:
            # Continuation line of the current list item.
            family = current_family

        if family == "numbered_restart" or (family != current_family and current):
            blocks.append((current_family, current))
            current = []
            current_family = ""
        if family:
            current_family = "numbered" if family == "numbered_restart" else family
            current.append((start, line))
    if current:
        blocks.append((current_family, current))

    for family, block in blocks:
        block_start = block[0][0]
        block_end = block[-1][0] + len(block[-1][1])
        if block_start <= match_offset < block_end:
            return _rank_in_block(block, match_offset, family)
    return None


# --------------------------------------------------------------------------
# Per-execution scoring + run aggregation
# --------------------------------------------------------------------------
def _entry_signals(
    *,
    entry: ProductEntry | CompetitorProductEntry,
    answer_text: str,
    normalized_answer: str,
    config: ProductScoringConfig,
) -> dict[str, Any]:
    first_offset = _first_offset(entry.aliases, normalized_answer)
    mentioned = first_offset is not None
    signals: dict[str, Any] = {
        "mentioned": mentioned,
        "first_offset": first_offset,
        "rank_position": None,
        "price_text": "",
        "price_value": None,
        "price_currency": "",
        "price_matches_catalog": None,
    }
    if not mentioned:
        return signals
    original_offset = _original_text_offset(entry.aliases, answer_text)
    if original_offset is None:
        return signals
    signals["rank_position"] = detect_product_rank(answer_text, original_offset)
    prices = extract_price_mentions(answer_text, original_offset)
    if prices:
        first = prices[0]
        signals["price_text"] = first["text"][:64]
        signals["price_value"] = first["value"]
        signals["price_currency"] = first["currency"]
        signals["price_matches_catalog"] = price_matches_catalog(
            first["value"],
            first["currency"],
            entry,
            tolerance_pct=config.price_tolerance_pct,
            tolerance_abs=config.price_tolerance_abs,
        )
    return signals


def score_product_execution(
    *, answer_text: str, config: ProductScoringConfig
) -> dict[str, Any]:
    """Per-execution deterministic product score.

    For every catalog entry (own + competitor): mention flag + first offset
    (normalized coordinates, mirroring the brand scorer), rank-in-list, the
    first windowed price mention, and catalog-price accuracy; plus headline
    counts. Applies the WHOLE frozen catalog to every response (mirrors
    ``_competitor_signals`` applying the full competitor registry).
    """
    normalized_answer = normalize_alias(answer_text)
    products = [
        {
            "product_id": entry.id,
            **_entry_signals(
                entry=entry,
                answer_text=answer_text,
                normalized_answer=normalized_answer,
                config=config,
            ),
        }
        for entry in config.products
    ]
    competitor_products = [
        {
            "competitor_product_id": entry.id,
            **_entry_signals(
                entry=entry,
                answer_text=answer_text,
                normalized_answer=normalized_answer,
                config=config,
            ),
        }
        for entry in config.competitor_products
    ]
    all_signals = products + competitor_products
    return {
        "products": products,
        "competitor_products": competitor_products,
        "own_product_mention_count": sum(1 for p in products if p["mentioned"]),
        "competitor_product_mention_count": sum(
            1 for p in competitor_products if p["mentioned"]
        ),
        # Entries (own + competitor) whose extracted price matched the catalog.
        "products_with_price_match": sum(
            1 for p in all_signals if p["price_matches_catalog"] is True
        ),
    }


def _rank_bucket(rank: int) -> str:
    for label, minimum, maximum in PRODUCT_RANK_BUCKETS:
        if rank >= minimum and (maximum is None or rank <= maximum):
            return label
    return PRODUCT_RANK_BUCKET_UNRANKED


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def aggregate_product_run(
    scores: list[dict[str, Any]], config: ProductScoringConfig
) -> dict[str, dict[str, Any]]:
    """Aggregate per-execution product scores into per-entry metrics.

    Pure function of the PERSISTED score dicts (invariant 7). Returns
    ``{entry_id: aggregate}`` for every catalog entry (zero-filled when
    unmentioned). SOV share = the entry's mention count over the total
    product + competitor-product mention volume (mirrors the brand SOV).
    Per-engine breakdowns are computed by the caller grouping executions by
    engine and re-calling this (mirrors ``aggregate_run``).
    """
    entries: list[tuple[str, str]] = [
        (entry.id, "products") for entry in config.products
    ] + [
        (entry.id, "competitor_products") for entry in config.competitor_products
    ]
    id_key = {
        "products": "product_id",
        "competitor_products": "competitor_product_id",
    }

    mentions: dict[str, list[dict[str, Any]]] = {
        entry_id: [] for entry_id, _ in entries
    }
    for score in scores:
        for section in ("products", "competitor_products"):
            for signals in score.get(section) or []:
                entry_id = str(signals.get(id_key[section]) or "")
                if entry_id in mentions and signals.get("mentioned"):
                    mentions[entry_id].append(signals)

    total_mentions = sum(len(rows) for rows in mentions.values())
    aggregates: dict[str, dict[str, Any]] = {}
    for entry_id, section in entries:
        rows = mentions[entry_id]
        mention_count = len(rows)
        ranks = [r["rank_position"] for r in rows if r.get("rank_position") is not None]
        distribution = {label: 0 for label, _, _ in PRODUCT_RANK_BUCKETS}
        for rank in ranks:
            distribution[_rank_bucket(rank)] += 1
        distribution[PRODUCT_RANK_BUCKET_UNRANKED] = mention_count - len(ranks)

        price_mentions = [r for r in rows if r.get("price_value") is not None]
        verifiable = [
            r for r in price_mentions if r.get("price_matches_catalog") is not None
        ]
        matches = [r for r in verifiable if r["price_matches_catalog"] is True]
        aggregates[entry_id] = {
            "kind": "product" if section == "products" else "competitor_product",
            "mention_count": mention_count,
            "sov_share": _rate(mention_count, total_mentions),
            "avg_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
            "rank_distribution": distribution,
            "price_mention_count": len(price_mentions),
            "price_match_count": len(matches),
            "price_accuracy_rate": (
                _rate(len(matches), len(verifiable)) if verifiable else None
            ),
        }
    return aggregates
