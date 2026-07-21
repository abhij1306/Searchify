"""Read scope for the durable Starter discovered-URL inventory.

Per-crawl ``SiteUrlObservation`` rows remain immutable provenance: carrying an
older crawl id in configuration never fabricates a new observation. Read paths
may UNION those explicitly frozen source crawls into the current Starter
dashboard so URLs do not vanish between discovery and a fresh analysis crawl.
Free/sample crawls always ignore inherited ids, preserving count/catalog
non-disclosure.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, union

from app.core.config.site_health import INVENTORY_SOURCE_CRAWL_IDS_KEY
from app.models.site_health import SiteCrawl, SiteUrlObservation


def inherited_inventory_crawl_ids(crawl: SiteCrawl) -> tuple[uuid.UUID, ...]:
    """Validated, de-duplicated source crawl ids frozen on a full crawl."""
    if crawl.sample_mode:
        return ()
    raw = (crawl.configuration or {}).get(INVENTORY_SOURCE_CRAWL_IDS_KEY, [])
    if not isinstance(raw, list):
        return ()
    result: list[uuid.UUID] = []
    for value in raw:
        try:
            parsed = uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            continue
        if parsed != crawl.id and parsed not in result:
            result.append(parsed)
    return tuple(result)


def freeze_inventory_lineage(source: SiteCrawl | None, *, limit: int) -> list[str]:
    """Newest-first bounded lineage to freeze onto a new Starter crawl."""
    if source is None or source.sample_mode:
        return []
    ids: list[uuid.UUID] = [source.id, *inherited_inventory_crawl_ids(source)]
    return [str(value) for value in dict.fromkeys(ids)][:limit]


def inventory_crawl_ids(crawl: SiteCrawl) -> tuple[uuid.UUID, ...]:
    """Current crawl followed by its explicit inventory source lineage."""
    return (crawl.id, *inherited_inventory_crawl_ids(crawl))


def inventory_site_url_subquery(crawl: SiteCrawl):
    """Scalar subquery of URL ids visible in this dashboard inventory."""
    statements = [
        select(SiteUrlObservation.site_url_id).where(
            SiteUrlObservation.crawl_id == crawl_id
        )
        for crawl_id in inventory_crawl_ids(crawl)
    ]
    if len(statements) == 1:
        return statements[0].scalar_subquery()
    return union(*statements).scalar_subquery()
