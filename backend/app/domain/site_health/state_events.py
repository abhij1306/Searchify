# Site Health state transitions + append-only crawl events (Task 3).
#
# Two responsibilities, mirroring the audit ``state_events``/``audit_state``
# split but kept in one module for the Site Health subsystem:
#
#   - Guarded status transitions for the three independent lifecycles (overall
#     crawl, discovery sub-state, analysis sub-state). An illegal transition
#     raises ``InvalidSiteCrawlTransition`` so a caller can never drive a crawl
#     into an impossible state (the normative tables come from the plan's
#     Persistence contract).
#   - ``record_crawl_event`` — appends an immutable ``SiteCrawlEvent`` (the SSE
#     source, invariant 3). Crucially, when the crawl is a Free sample
#     (``count_disclosure`` False), the payload is stripped of any
#     total/frontier/overflow-bearing keys so no hidden full-site signal can
#     ever leave the backend through an event (product non-disclosure contract).
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.site_health import (
    ANALYSIS_STATUS_CANCELLED,
    ANALYSIS_STATUS_COMPLETED,
    ANALYSIS_STATUS_FAILED,
    ANALYSIS_STATUS_PARTIALLY_COMPLETED,
    ANALYSIS_STATUS_PENDING,
    ANALYSIS_STATUS_RUNNING,
    CRAWL_STATUS_CANCELLED,
    CRAWL_STATUS_COMPLETED,
    CRAWL_STATUS_DRAFT,
    CRAWL_STATUS_FAILED,
    CRAWL_STATUS_PARTIALLY_COMPLETED,
    CRAWL_STATUS_QUEUED,
    CRAWL_STATUS_RUNNING,
    CRAWL_STATUS_VALIDATING,
    DISCOVERY_STATUS_CANCELLED,
    DISCOVERY_STATUS_COMPLETED,
    DISCOVERY_STATUS_FAILED,
    DISCOVERY_STATUS_PENDING,
    DISCOVERY_STATUS_RUNNING,
    DISCOVERY_STATUS_SAMPLE_COMPLETED,
)
from app.models.site_health import SiteCrawl, SiteCrawlEvent

# Keys that carry (or could reconstruct) a full-site total/frontier/overflow
# signal. For a Free sample crawl these are removed from every event payload so
# the count-disclosure contract holds even through the events stream.
_TOTAL_BEARING_KEYS: frozenset[str] = frozenset(
    {
        "total_url_count",
        "total",
        "frontier_size",
        "frontier",
        "overflow",
        "overflow_count",
        "discarded",
        "discarded_count",
        "has_more_site_urls",
        "estimated_total",
        "sitemap_url_count",
        "discovered_url_count",
        "discovered_total",
    }
)


class InvalidSiteCrawlTransition(ValueError):
    """Raised when a Site Health status transition is not permitted."""


_CRAWL_TRANSITIONS: dict[str, set[str]] = {
    CRAWL_STATUS_DRAFT: {CRAWL_STATUS_VALIDATING, CRAWL_STATUS_CANCELLED},
    CRAWL_STATUS_VALIDATING: {
        CRAWL_STATUS_QUEUED,
        CRAWL_STATUS_FAILED,
        CRAWL_STATUS_CANCELLED,
    },
    CRAWL_STATUS_QUEUED: {CRAWL_STATUS_RUNNING, CRAWL_STATUS_CANCELLED},
    CRAWL_STATUS_RUNNING: {
        CRAWL_STATUS_COMPLETED,
        CRAWL_STATUS_PARTIALLY_COMPLETED,
        CRAWL_STATUS_FAILED,
        CRAWL_STATUS_CANCELLED,
    },
    CRAWL_STATUS_COMPLETED: set(),
    CRAWL_STATUS_PARTIALLY_COMPLETED: set(),
    CRAWL_STATUS_FAILED: set(),
    CRAWL_STATUS_CANCELLED: set(),
}

_DISCOVERY_TRANSITIONS: dict[str, set[str]] = {
    DISCOVERY_STATUS_PENDING: {
        DISCOVERY_STATUS_RUNNING,
        DISCOVERY_STATUS_CANCELLED,
    },
    DISCOVERY_STATUS_RUNNING: {
        DISCOVERY_STATUS_COMPLETED,
        DISCOVERY_STATUS_SAMPLE_COMPLETED,
        DISCOVERY_STATUS_FAILED,
        DISCOVERY_STATUS_CANCELLED,
    },
    DISCOVERY_STATUS_COMPLETED: set(),
    DISCOVERY_STATUS_SAMPLE_COMPLETED: set(),
    DISCOVERY_STATUS_FAILED: set(),
    DISCOVERY_STATUS_CANCELLED: set(),
}

_ANALYSIS_TRANSITIONS: dict[str, set[str]] = {
    ANALYSIS_STATUS_PENDING: {
        ANALYSIS_STATUS_RUNNING,
        ANALYSIS_STATUS_CANCELLED,
    },
    ANALYSIS_STATUS_RUNNING: {
        ANALYSIS_STATUS_COMPLETED,
        ANALYSIS_STATUS_PARTIALLY_COMPLETED,
        ANALYSIS_STATUS_FAILED,
        ANALYSIS_STATUS_CANCELLED,
    },
    ANALYSIS_STATUS_COMPLETED: set(),
    ANALYSIS_STATUS_PARTIALLY_COMPLETED: set(),
    ANALYSIS_STATUS_FAILED: set(),
    ANALYSIS_STATUS_CANCELLED: set(),
}


def _normalize(value: str) -> str:
    return str(value).strip().lower()


def _transition(table: dict[str, set[str]], current: str, target: str) -> str:
    cur = _normalize(current)
    tgt = _normalize(target)
    if cur not in table:
        raise InvalidSiteCrawlTransition(f"unknown status: {cur}")
    if cur == tgt:
        return tgt
    if tgt not in table[cur]:
        raise InvalidSiteCrawlTransition(f"invalid transition: {cur} -> {tgt}")
    return tgt


def transition_crawl_status(current: str, target: str) -> str:
    return _transition(_CRAWL_TRANSITIONS, current, target)


def transition_discovery_status(current: str, target: str) -> str:
    return _transition(_DISCOVERY_TRANSITIONS, current, target)


def transition_analysis_status(current: str, target: str) -> str:
    return _transition(_ANALYSIS_TRANSITIONS, current, target)


def apply_crawl_status(crawl: SiteCrawl, target: str) -> str:
    crawl.status = transition_crawl_status(crawl.status, target)
    return crawl.status


def apply_discovery_status(crawl: SiteCrawl, target: str) -> str:
    crawl.discovery_status = transition_discovery_status(crawl.discovery_status, target)
    return crawl.discovery_status


def apply_analysis_status(crawl: SiteCrawl, target: str) -> str:
    crawl.analysis_status = transition_analysis_status(crawl.analysis_status, target)
    return crawl.analysis_status


def redact_event_payload(
    payload: dict | None, *, count_disclosure: bool
) -> dict | None:
    """Strip total/frontier/overflow keys from a Free-sample event payload.

    Starter (``count_disclosure`` True) keeps the payload as-is. Free removes
    every total-bearing key so an event can never leak a hidden full-site count
    (product non-disclosure contract). Returns a NEW dict (never mutates the
    caller's).
    """
    if payload is None:
        return None
    if count_disclosure:
        return dict(payload)
    return {
        key: value for key, value in payload.items() if key not in _TOTAL_BEARING_KEYS
    }


def record_crawl_event(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    event_type: str,
    message: str = "",
    payload: dict | None = None,
    count_disclosure: bool = True,
) -> SiteCrawlEvent:
    """Append an immutable crawl event. Caller owns the commit (invariant 3).

    ``count_disclosure`` gates whether the payload may carry totals: pass the
    crawl's frozen entitlement flag so Free events are redacted at the point of
    write (defence in depth alongside the API serializer).
    """
    event = SiteCrawlEvent(
        crawl_id=crawl_id,
        event_type=event_type,
        message=message,
        payload=redact_event_payload(payload, count_disclosure=count_disclosure),
    )
    session.add(event)
    return event
