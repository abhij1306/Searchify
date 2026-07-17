# Site Health domain DTOs used by the Task 3 discovery pipeline.
#
# Small, immutable value types shared by ``discovery``/``planner``/the worker.
# These are internal domain contracts (not HTTP request/response models — those
# arrive with the Task 6 API); they carry the deterministic frontier-ordering
# key and the bounded discovery output the worker persists.
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FrontierCandidate:
    """A canonical in-scope URL awaiting admission, with its ordering key.

    Deterministic frontier order is ``(parent_position, link_ordinal,
    url_hash)`` under the crawl's stored seed: the seed fixes the parent order
    and each parent lists its links in document order, so the same seed + same
    site always admits URLs in the same order (invariant 9).
    """

    url: str
    url_hash: str
    depth: int
    source_kind: str
    parent_position: int = 0
    link_ordinal: int = 0

    @property
    def order_key(self) -> tuple[int, int, str]:
        return (self.parent_position, self.link_ordinal, self.url_hash)


@dataclass(frozen=True, slots=True)
class DiscoveredLink:
    """One in-scope link extracted from a fetched page (document order)."""

    url: str
    url_hash: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class DiscoveryOutput:
    """The bounded result of executing one discover task.

    ``title``/``content_type``/``status_code``/``final_url`` describe the
    fetched page; ``links`` are the canonical in-scope links (already narrowed);
    ``redirect_chain`` records re-validated hops. The worker turns this into a
    ``SiteUrlObservation`` + admits ``links`` into the frontier.
    """

    requested_url: str
    final_url: str
    status_code: int | None
    content_type: str
    title: str
    links: tuple[DiscoveredLink, ...] = ()
    redirect_chain: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """The outcome of a batched frontier admission attempt.

    ``admitted`` is the count of NEW ``SiteUrl`` identities created this batch;
    ``sample_capped`` is True when a Free crawl hit its workspace-wide allowance
    and admission stopped. ``site_url_ids`` maps ``url_hash -> SiteUrl.id`` for
    the URLs admitted (or already present) so the caller can write observations
    and enqueue child tasks.
    """

    admitted: int
    sample_capped: bool
    site_url_ids: dict[str, str] = field(default_factory=dict)
