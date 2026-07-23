"""Derivation: import artifact -> ``IntegrationMetricRow`` (I9, spec §4.5).

A pure PROJECTION invoked by the integrations worker after the raw import
lands — never a second provider fetch (invariant 7), and this module is the
single writer of ``IntegrationMetricRow`` (invariant 3).

- **Mapping resolution** — the run's property (the connection's
  ``account_ref``) is resolved through the ACTIVE
  ``IntegrationPropertyMapping`` to its ``project_id``; an unmapped property
  fails the run with ``unmapped_property`` and is NEVER guessed
  (spec §4 step 5).
- **Transform** — every artifact payload row becomes one
  ``IntegrationMetricRow`` carrying the full provenance triple
  (invariant 4): ``source_artifact_id`` + ``INTEGRATION_IMPORTER_VERSION``
  (transform-code version) + the run's ``resync_seq`` (data-run revision).
  ``dimension_key`` packs ALL declared dimension values — date included,
  in template order — via the config-owned ``pack_dimension_key``
  (contract C1, invariant 2; analytics consumers peel the trailing date).
- **Idempotent** — rows insert via ``ON CONFLICT DO NOTHING`` on the
  identity tuple, so a retried derivation (resume-after-crash) is a dedup
  no-op, never an overwrite. A re-sync writes NEW rows at the higher
  ``resync_seq``; old revisions are retained.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.integrations import (
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_IMPORTER_VERSION,
    MAPPING_STATUS_ACTIVE,
    IntegrationDatasetTemplate,
    pack_dimension_key,
)
from app.models.integrations import (
    IntegrationConnection,
    IntegrationImportArtifact,
    IntegrationMetricRow,
    IntegrationPropertyMapping,
    IntegrationSyncRun,
)

# The date dimension literal every C1 template declares (trailing).
_DATE_DIMENSION = "date"
# GA4 ``runReport`` date values are compact ("20260720"); GSC dates are ISO.
_GA4_COMPACT_DATE_LEN = 8


class UnmappedPropertyError(RuntimeError):
    """The run's property has no ACTIVE mapping — fail, never guess."""


@dataclass(frozen=True)
class DerivedRun:
    """The outcome of deriving one run's artifacts."""

    project_id: uuid.UUID
    metric_row_count: int
    artifact_ids: tuple[uuid.UUID, ...]


async def resolve_active_mapping(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    provider: str,
    property_ref: str,
) -> IntegrationPropertyMapping:
    """Resolve the ACTIVE mapping owning ``(workspace, provider, property)``.

    The partial unique index guarantees at most one ACTIVE owner, so this
    never picks between candidates; zero owners raises
    ``UnmappedPropertyError`` (the run fails, the property is never
    guessed).
    """
    result = await session.execute(
        select(IntegrationPropertyMapping).where(
            IntegrationPropertyMapping.workspace_id == workspace_id,
            IntegrationPropertyMapping.provider == provider,
            IntegrationPropertyMapping.property_ref == property_ref,
            IntegrationPropertyMapping.status == MAPPING_STATUS_ACTIVE,
        )
    )
    mapping = result.scalar_one_or_none()
    if mapping is None:
        raise UnmappedPropertyError(
            f"no active property mapping for {provider}:{property_ref!r}"
        )
    return mapping


def _parse_row_date(raw: str) -> date | None:
    """Parse one provider date-dimension value (ISO, or GA4 compact)."""
    text = raw.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    if len(text) == _GA4_COMPACT_DATE_LEN and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    return None


def build_metric_row_values(
    *,
    template: IntegrationDatasetTemplate,
    run: IntegrationSyncRun,
    mapping: IntegrationPropertyMapping,
    artifact: IntegrationImportArtifact,
) -> list[dict[str, Any]]:
    """Transform one artifact's payload rows into metric-row column values.

    Pure (no DB). A payload row whose ``keys`` do not match the template's
    declared dimension arity, or whose date value is unparseable, is
    skipped — malformed provider data is dropped, never guessed.
    """
    payload = artifact.payload or {}
    payload_rows = payload.get("rows") or []
    date_index = template.dimensions.index(_DATE_DIMENSION)
    values: list[dict[str, Any]] = []
    for payload_row in payload_rows:
        if not isinstance(payload_row, dict):
            continue
        keys = payload_row.get("keys")
        if not isinstance(keys, list) or len(keys) != len(template.dimensions):
            continue
        row_date = _parse_row_date(str(keys[date_index]))
        if row_date is None:
            continue
        metrics = {
            name: payload_row[name] for name in template.metrics if name in payload_row
        }
        values.append(
            {
                "workspace_id": run.workspace_id,
                "project_id": mapping.project_id,
                "property_ref": mapping.property_ref,
                "provider": artifact.provider,
                "dataset": artifact.dataset,
                "date": row_date,
                # ALL declared dimension values, date included (C1).
                "dimension_key": pack_dimension_key([str(key) for key in keys]),
                "metrics": metrics,
                "source_artifact_id": artifact.id,
                "resync_seq": run.resync_seq,
                "importer_version": INTEGRATION_IMPORTER_VERSION,
            }
        )
    return values


async def derive_run(
    session: AsyncSession,
    *,
    run: IntegrationSyncRun,
    connection: IntegrationConnection,
    artifacts: list[IntegrationImportArtifact],
) -> DerivedRun:
    """Derive one run's metric rows inside the caller's transaction.

    Resolves the active property mapping (raising ``UnmappedPropertyError``
    when absent), transforms every artifact's rows, and inserts them
    conflict-safely on the identity tuple. The caller (the integrations
    worker) owns the transaction boundary + the run-row lock and performs
    the C5 ``enqueue_post_sync_projections`` call as the final step.
    """
    mapping = await resolve_active_mapping(
        session,
        workspace_id=run.workspace_id,
        provider=connection.provider,
        property_ref=connection.account_ref,
    )
    values: list[dict[str, Any]] = []
    for artifact in artifacts:
        template = INTEGRATION_DATASET_TEMPLATES.get(artifact.dataset)
        if template is None:
            # An unknown dataset id is skipped, never guessed (the config
            # templates are the only dataset vocabulary).
            continue
        values.extend(
            build_metric_row_values(
                template=template, run=run, mapping=mapping, artifact=artifact
            )
        )
    if values:
        await session.execute(
            pg_insert(IntegrationMetricRow)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[
                    "project_id",
                    "property_ref",
                    "provider",
                    "dataset",
                    "date",
                    "dimension_key",
                    "resync_seq",
                ]
            )
        )
    return DerivedRun(
        project_id=mapping.project_id,
        metric_row_count=len(values),
        artifact_ids=tuple(artifact.id for artifact in artifacts),
    )
