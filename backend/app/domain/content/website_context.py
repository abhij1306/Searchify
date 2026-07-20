"""Deterministic Website-context projection from persisted Site Health evidence.

Pure DB projection (invariant 7): no fetch, no extraction, no provider call.
Selects the newest terminal crawl with usable artifacts, orders pages
deterministically (homepage -> active monitored -> stable URL), emits an
allowlist-only subset of each page's ``normalized_facts``, sanitises and caps
every field plus the total character budget, and records full provenance so
the result UI can show exactly which crawl (and how fresh) grounded the
content. The same inputs always produce the same snapshot.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, and_, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.content import (
    CONTENT_CONTEXT_FIELD_MAX_CHARS,
    CONTENT_CONTEXT_MAX_CHARS,
    CONTENT_CONTEXT_MAX_PAGES,
    CONTENT_CONTEXT_PER_PAGE_BODY_CHARS,
    CONTEXT_MAX_H1,
    CONTEXT_MAX_H2,
    CONTEXT_STATUS_INCLUDED,
    CONTEXT_STATUS_UNAVAILABLE,
)
from app.core.config.site_health import CRAWL_TERMINAL_STATUSES
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteFetchArtifact,
    SiteHealthProfile,
    SitePageAnalysis,
    SiteUrl,
)

# Control/non-printable chars stripped from every emitted string; the
# whitespace collapse in ``_clean`` then folds ALL whitespace (newlines
# included) to single spaces.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")


def _facts_usable() -> ColumnElement[bool]:
    """SQL predicate: artifact facts are a non-empty JSON object.

    An explicit Python ``None`` persists as JSON ``null`` (not SQL NULL) in a
    JSONB column, so an ``IS NOT NULL`` check alone would treat factless
    artifacts as usable. ``jsonb_typeof`` is NULL for SQL NULL and ``'null'``
    for JSON null, so one comparison covers both. ``{}`` must also be
    excluded here — the in-memory page filter drops empty facts, so a crawl
    admitted on ``{}`` alone would be selected and then yield zero pages
    instead of falling back to an older usable crawl.
    """
    facts = SiteFetchArtifact.normalized_facts
    return and_(
        func.jsonb_typeof(facts) == "object",
        facts != cast({}, JSONB),
    )


@dataclass(frozen=True)
class WebsiteContext:
    """The frozen context snapshot + provenance for one generation."""

    status: str
    # Ordered allowlisted page blocks (empty when unavailable/disabled).
    pages: list[dict] = field(default_factory=list)
    # Provenance: crawl identity + versions + source ids + counts.
    summary: dict | None = None

    def snapshot(self) -> dict:
        """The JSON persisted on the ``ContentGeneration`` row."""
        return {
            "status": self.status,
            "pages": self.pages,
            "summary": self.summary,
        }


def _clean(value: object, *, max_chars: int) -> str:
    """Strip control chars, collapse whitespace, enforce the char cap."""
    text = _CONTROL_CHARS.sub("", str(value or ""))
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:max_chars]


async def _newest_usable_crawl(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> SiteCrawl | None:
    """Newest terminal crawl with >=1 analysis over non-empty facts.

    ``completed``/``partially_completed`` naturally qualify; ``failed``/
    ``cancelled`` qualify only when they still produced usable artifacts —
    the EXISTS predicate enforces that uniformly, in one bounded query
    (no per-crawl scan).
    """
    usable_exists = (
        select(SitePageAnalysis.id)
        .join(
            SiteFetchArtifact,
            SiteFetchArtifact.id == SitePageAnalysis.artifact_id,
        )
        .where(SitePageAnalysis.crawl_id == SiteCrawl.id)
        .where(_facts_usable())
        .exists()
    )
    return await session.scalar(
        select(SiteCrawl)
        .where(SiteCrawl.workspace_id == workspace_id)
        .where(SiteCrawl.project_id == project_id)
        .where(SiteCrawl.status.in_(list(CRAWL_TERMINAL_STATUSES)))
        .where(usable_exists)
        .order_by(SiteCrawl.created_at.desc(), SiteCrawl.id.desc())
        .limit(1)
    )


async def build_website_context(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> WebsiteContext:
    """Build the bounded, deterministic, sanitised context projection."""
    crawl = await _newest_usable_crawl(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if crawl is None:
        return WebsiteContext(status=CONTEXT_STATUS_UNAVAILABLE)

    profile = await session.scalar(
        select(SiteHealthProfile).where(SiteHealthProfile.project_id == project_id)
    )
    root_url = (profile.root_url if profile else "") or ""
    root_host = (profile.root_host if profile else "") or ""

    # All analysed pages of the crawl with their artifacts + URL identities.
    rows = (
        await session.execute(
            select(SitePageAnalysis, SiteFetchArtifact, SiteUrl)
            .join(
                SiteFetchArtifact,
                SiteFetchArtifact.id == SitePageAnalysis.artifact_id,
            )
            .join(SiteUrl, SiteUrl.id == SitePageAnalysis.site_url_id)
            .where(SitePageAnalysis.crawl_id == crawl.id)
            .where(_facts_usable())
        )
    ).all()
    usable = [
        (analysis, artifact, site_url)
        for analysis, artifact, site_url in rows
        if artifact.normalized_facts
    ]
    if not usable:
        return WebsiteContext(status=CONTEXT_STATUS_UNAVAILABLE)

    # Active monitored membership for this project (inactive rows ignored).
    monitored_ids = set(
        (
            await session.scalars(
                select(MonitoredSiteUrl.site_url_id)
                .where(MonitoredSiteUrl.project_id == project_id)
                .where(MonitoredSiteUrl.active.is_(True))
            )
        ).all()
    )

    def _is_homepage(site_url: SiteUrl) -> bool:
        normalized = site_url.normalized_url
        if root_url and normalized.rstrip("/") == root_url.rstrip("/"):
            return True
        if root_host:
            stripped = re.sub(r"^https?://", "", normalized).rstrip("/")
            return stripped == root_host
        return False

    def _sort_key(entry: tuple) -> tuple:
        _analysis, _artifact, site_url = entry
        if _is_homepage(site_url):
            tier = 0
        elif site_url.id in monitored_ids:
            tier = 1
        else:
            tier = 2
        # Ties broken by normalized_url then id — fully deterministic.
        return (tier, site_url.normalized_url, str(site_url.id))

    usable.sort(key=_sort_key)
    usable = usable[:CONTENT_CONTEXT_MAX_PAGES]

    pages: list[dict] = []
    site_url_ids: list[str] = []
    artifact_ids: list[str] = []
    content_hashes: list[str] = []
    fetched_ats: list[str | None] = []
    extractor_version = ""
    analyzer_version = ""
    total_chars = 0
    for analysis, artifact, site_url in usable:
        facts = artifact.normalized_facts or {}
        headings = facts.get("headings") or {}
        body = facts.get("body") or {}
        page = {
            "final_url": _clean(
                artifact.final_url or site_url.normalized_url,
                max_chars=CONTENT_CONTEXT_FIELD_MAX_CHARS,
            ),
            "title": _clean(
                facts.get("title"), max_chars=CONTENT_CONTEXT_FIELD_MAX_CHARS
            ),
            "meta_description": _clean(
                facts.get("meta_description"),
                max_chars=CONTENT_CONTEXT_FIELD_MAX_CHARS,
            ),
            "h1": [
                _clean(h, max_chars=CONTENT_CONTEXT_FIELD_MAX_CHARS)
                for h in (headings.get("h1_texts") or [])[:CONTEXT_MAX_H1]
            ],
            "h2": [
                _clean(h, max_chars=CONTENT_CONTEXT_FIELD_MAX_CHARS)
                for h in (headings.get("h2_texts") or [])[:CONTEXT_MAX_H2]
            ],
            "body_text": _clean(
                body.get("text"), max_chars=CONTENT_CONTEXT_PER_PAGE_BODY_CHARS
            ),
        }
        page_chars = sum(
            len(v) if isinstance(v, str) else sum(len(s) for s in v)
            for v in page.values()
        )
        # Total budget: drop trailing pages (deterministic order) once hit.
        if total_chars + page_chars > CONTENT_CONTEXT_MAX_CHARS:
            break
        total_chars += page_chars
        pages.append(page)
        site_url_ids.append(str(site_url.id))
        artifact_ids.append(str(artifact.id))
        content_hashes.append(artifact.content_hash or "")
        fetched_at = artifact.fetched_at
        fetched_ats.append(fetched_at.isoformat() if fetched_at else None)
        extractor_version = extractor_version or artifact.extractor_version
        analyzer_version = analyzer_version or analysis.analyzer_version

    if not pages:
        return WebsiteContext(status=CONTEXT_STATUS_UNAVAILABLE)

    summary = {
        "crawl_id": str(crawl.id),
        "crawl_completed_at": (
            crawl.completed_at.isoformat() if crawl.completed_at else None
        ),
        "extractor_version": extractor_version,
        "analyzer_version": analyzer_version,
        "page_count": len(pages),
        "char_count": total_chars,
        "site_url_ids": site_url_ids,
        "artifact_ids": artifact_ids,
        "content_hashes": content_hashes,
        "fetched_at": fetched_ats,
    }
    return WebsiteContext(status=CONTEXT_STATUS_INCLUDED, pages=pages, summary=summary)
