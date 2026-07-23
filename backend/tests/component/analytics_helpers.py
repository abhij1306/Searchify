"""Shared seed helpers for the LLM-Analytics component tests (A5+).

Builds a workspace + project plus the integrations-owned import graph
(OAuth grant -> connection -> sync run -> immutable import artifact ->
derived metric rows) directly through the ORM (no HTTP, no provider I/O),
so the analytics executors project over real fixture rows exactly like the
C5 chain sees them after a sync. ``dimension_key`` values are packed ONLY
via the config-owned ``pack_dimension_key`` in the dataset template's
declared dimension order (contract C1, invariant 2).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.integrations import (
    DATASET_GA4_REFERRER_DAILY,
    GRANT_STATUS_CONNECTED,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_TRANSPORT_GOOGLE,
    pack_dimension_key,
)
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
from app.models.integrations import (
    IntegrationConnection,
    IntegrationImportArtifact,
    IntegrationMetricRow,
    IntegrationOAuthGrant,
    IntegrationSyncRun,
)
from app.models.project import Project
from app.models.workspace import Workspace

DEFAULT_WINDOW = (date(2026, 7, 20), date(2026, 7, 22))
DEFAULT_PROPERTY_REF = "properties/123456789"


@dataclass
class ImportSeed:
    """The ids of one seeded import graph (grant -> ... -> artifact)."""

    workspace_id: uuid.UUID
    project_id: uuid.UUID
    grant_id: uuid.UUID
    connection_id: uuid.UUID
    sync_run_id: uuid.UUID
    artifact_id: uuid.UUID
    property_ref: str
    dataset: str
    metric_row_ids: list[uuid.UUID] = field(default_factory=list)


async def seed_workspace_project(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a bare workspace + project and commit."""
    workspace = Workspace(name="Analytics WS")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Analytics Project")
    session.add(project)
    await session.flush()
    await session.commit()
    return workspace.id, project.id


async def seed_ga4_import(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset: str = DATASET_GA4_REFERRER_DAILY,
    window: tuple[date, date] = DEFAULT_WINDOW,
    resync_seq: int = 0,
    property_ref: str = DEFAULT_PROPERTY_REF,
    connection: IntegrationConnection | None = None,
) -> ImportSeed:
    """Seed one GA4 import graph down to a single-page import artifact.

    The sync run is seeded in a TERMINAL status (a completed sync) so a
    re-sync of the same window at a higher ``resync_seq`` never collides
    with the active-window partial unique index. An existing ``connection``
    may be passed to seed a second run/artifact on the same connection.
    """
    template = INTEGRATION_DATASET_TEMPLATES[dataset]
    if connection is None:
        grant = IntegrationOAuthGrant(
            workspace_id=workspace_id,
            transport=INTEGRATION_TRANSPORT_GOOGLE,
            access_token_encrypted="fernet-access",
            refresh_token_encrypted="fernet-refresh",
            granted_scopes=["scope-ga4"],
            status=GRANT_STATUS_CONNECTED,
        )
        session.add(grant)
        await session.flush()
        connection = IntegrationConnection(
            workspace_id=workspace_id,
            grant_id=grant.id,
            provider=INTEGRATION_PROVIDER_GA4,
            label="ga4 connection",
            account_ref="ga4-account-1",
        )
        session.add(connection)
        await session.flush()
        grant_id = grant.id
    else:
        grant_id = connection.grant_id
    run = IntegrationSyncRun(
        workspace_id=workspace_id,
        connection_id=connection.id,
        window_start=window[0],
        window_end=window[1],
        resync_seq=resync_seq,
        idempotency_key=uuid.uuid4().hex,
        status=TASK_STATUS_SUCCEEDED,
    )
    session.add(run)
    await session.flush()
    artifact = IntegrationImportArtifact(
        workspace_id=workspace_id,
        sync_run_id=run.id,
        connection_id=connection.id,
        provider=INTEGRATION_PROVIDER_GA4,
        dataset=dataset,
        query_snapshot={
            "dimensions": list(template.dimensions),
            "metrics": list(template.metrics),
        },
        payload_hash=uuid.uuid4().hex * 2,
        row_count=0,
        payload={"rows": []},
    )
    session.add(artifact)
    await session.flush()
    return ImportSeed(
        workspace_id=workspace_id,
        project_id=project_id,
        grant_id=grant_id,
        connection_id=connection.id,
        sync_run_id=run.id,
        artifact_id=artifact.id,
        property_ref=property_ref,
        dataset=dataset,
    )


async def seed_metric_row(
    session: AsyncSession,
    *,
    seed: ImportSeed,
    row_date: date,
    dimension_values: Sequence[str],
    metrics: dict | None = None,
    resync_seq: int = 0,
    dataset: str | None = None,
) -> IntegrationMetricRow:
    """Seed one derived metric row under the seed's artifact.

    ``dimension_values`` MUST be in the dataset template's declared order
    (date included, per the C1 template); the key is packed by the
    config-owned ``pack_dimension_key`` — never re-implemented here.
    """
    row_dataset = dataset or seed.dataset
    template = INTEGRATION_DATASET_TEMPLATES[row_dataset]
    if len(dimension_values) != len(template.dimensions):
        raise ValueError(
            f"{row_dataset} expects {len(template.dimensions)} dimension values "
            f"{template.dimensions}, got {len(dimension_values)}"
        )
    row = IntegrationMetricRow(
        workspace_id=seed.workspace_id,
        project_id=seed.project_id,
        property_ref=seed.property_ref,
        provider=INTEGRATION_PROVIDER_GA4,
        dataset=row_dataset,
        date=row_date,
        dimension_key=pack_dimension_key(dimension_values),
        metrics=metrics if metrics is not None else {},
        source_artifact_id=seed.artifact_id,
        resync_seq=resync_seq,
    )
    session.add(row)
    await session.flush()
    seed.metric_row_ids.append(row.id)
    return row
