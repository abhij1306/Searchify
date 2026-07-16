#!/usr/bin/env python
# Operator / development command: assign a Site Health capability to a workspace.
#
# Site Health entitlements are capability-based (``free`` / ``starter``), never
# a marketing plan display name. This command is the manual/dev path to grant a
# workspace the Starter capability (or reset it to Free) by workspace UUID. It
# delegates to the same ``app.domain.site_health.entitlements.set_entitlement``
# domain service that production billing may call later, so behavior stays
# consistent, and it emits a single audit-safe log line recording the change.
#
# Usage (from ``backend/`` with ``DATABASE_URL`` pointing at the target DB):
#
#     uv run python -m scripts.set_site_health_entitlement \
#         <workspace_uuid> <free|starter>
#
# See ``docs/DEVELOPMENT.md`` for the local runbook.
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid

from app.core.config.site_health import (
    SITE_HEALTH_CAPABILITIES,
    normalize_capability,
)
from app.core.database import SessionLocal, dispose_engine
from app.domain.site_health.entitlements import set_entitlement

logger = logging.getLogger("scripts.set_site_health_entitlement")


async def _run(workspace_id: uuid.UUID, capability: str) -> None:
    async with SessionLocal() as session:
        row = await set_entitlement(session, workspace_id, capability)
        await session.commit()
        # Audit-safe log line: identifies the workspace, resolved capability,
        # frozen limits, and revision — never any secret.
        logger.info(
            "site_health.entitlement.set workspace_id=%s capability=%s "
            "monitored_url_limit=%s discovery_mode=%s count_disclosure=%s "
            "capability_revision=%s",
            workspace_id,
            row.plan_key,
            row.monitored_url_limit,
            row.discovery_mode,
            row.count_disclosure,
            row.capability_revision,
        )
    await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    parser = argparse.ArgumentParser(
        description=(
            "Assign a Site Health capability (free|starter) to a workspace "
            "by UUID. Development/operator command; production billing may "
            "call the same domain service."
        )
    )
    parser.add_argument("workspace_id", help="Target workspace UUID.")
    parser.add_argument(
        "capability",
        help="Capability key to assign.",
        choices=sorted(SITE_HEALTH_CAPABILITIES),
    )
    args = parser.parse_args(argv)

    try:
        workspace_id = uuid.UUID(str(args.workspace_id))
    except ValueError:
        parser.error(f"invalid workspace UUID: {args.workspace_id!r}")

    capability = normalize_capability(args.capability)
    asyncio.run(_run(workspace_id, capability))
    return 0


if __name__ == "__main__":
    sys.exit(main())
