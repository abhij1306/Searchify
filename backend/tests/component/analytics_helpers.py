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
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION
from app.core.config.analytics import (
    AI_REFERRAL_RULE_VERSION,
    AI_SOURCE_OTHER,
    REFERRAL_SANITIZE_VERSION,
)
from app.core.config.audits import AUDIT_STATUS_COMPLETED
from app.core.config.integrations import (
    DATASET_GA4_REFERRER_DAILY,
    GRANT_STATUS_CONNECTED,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_TRANSPORT_GOOGLE,
    pack_dimension_key,
)
from app.core.config.provider_catalog import ENGINE_GEMINI
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
from app.models.analysis import MetricSnapshot, ResponseAnalysis
from app.models.analytics import ReferralClassification, ReferralEvent
from app.models.audit import (
    Audit,
    AuditEngineSnapshot,
    AuditPromptSnapshot,
    AuditTask,
)
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
    provider: str = INTEGRATION_PROVIDER_GA4


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
    provider: str = INTEGRATION_PROVIDER_GA4,
) -> ImportSeed:
    """Seed one import graph down to a single-page import artifact.

    The sync run is seeded in a TERMINAL status (a completed sync) so a
    re-sync of the same window at a higher ``resync_seq`` never collides
    with the active-window partial unique index. An existing ``connection``
    may be passed to seed a second run/artifact on the same connection.
    ``provider`` selects the connection/artifact/row provider (GSC rides
    the same Google grant/transport as GA4) so traffic tests can seed GSC
    page/query imports with the same helper.
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
            provider=provider,
            label=f"{provider} connection",
            account_ref=f"{provider}-account-1",
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
        provider=provider,
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
        provider=provider,
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
    provider: str | None = None,
) -> IntegrationMetricRow:
    """Seed one derived metric row under the seed's artifact.

    ``dimension_values`` MUST be in the dataset template's declared order
    (date included, per the C1 template); the key is packed by the
    config-owned ``pack_dimension_key`` — never re-implemented here.
    ``provider`` defaults to the seed's provider so GSC imports seed GSC
    rows.
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
        provider=provider or seed.provider,
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


async def seed_referral_event(
    session: AsyncSession,
    *,
    seed: ImportSeed,
    occurred_at: datetime,
    referrer_host: str = "",
    referrer_url: str = "",
    landing_url: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    user_agent: str = "",
    source_metric_row_id: uuid.UUID | None = None,
) -> ReferralEvent:
    """Seed one ReferralEvent directly (bypassing the ingest projection).

    For the classify/retention tests that need events with exact signals or
    exact ``occurred_at`` values without driving the metric-row ingest. The
    ``content_hash`` is a random unique token (dedupe is not under test
    here); ``sanitize_version`` is stamped like the real projection.
    ``source_metric_row_id`` links the event to its derived metric row so
    the snapshot builder (A8) can join the session measure + resync
    identity exactly like the ingest projection writes it.
    """
    event = ReferralEvent(
        workspace_id=seed.workspace_id,
        project_id=seed.project_id,
        source=INTEGRATION_PROVIDER_GA4,
        import_id=seed.artifact_id,
        source_metric_row_id=source_metric_row_id,
        occurred_at=occurred_at,
        landing_url=landing_url,
        referrer_host=referrer_host,
        referrer_url=referrer_url,
        utm_source=utm_source,
        utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        user_agent=user_agent,
        session_id_hash="",
        raw=None,
        content_hash=uuid.uuid4().hex * 2,
        sanitize_version=REFERRAL_SANITIZE_VERSION,
    )
    session.add(event)
    await session.flush()
    return event


async def seed_referral_classification(
    session: AsyncSession,
    *,
    event: ReferralEvent,
    is_ai_referral: bool = False,
    ai_source: str = AI_SOURCE_OTHER,
    logical_engine: str | None = None,
    matched_rule_id: str = "",
    match_signal: str = "",
    confidence: str = "",
) -> ReferralClassification:
    """Seed one classification row directly (retention/snapshot/API tests).

    The classify executor is the single WRITER in production; tests seed the
    row straight to set up fixtures without running the chain.
    """
    classification = ReferralClassification(
        workspace_id=event.workspace_id,
        project_id=event.project_id,
        referral_event_id=event.id,
        is_ai_referral=is_ai_referral,
        ai_source=ai_source,
        logical_engine=logical_engine,
        matched_rule_id=matched_rule_id,
        match_signal=match_signal,
        confidence=confidence,
        rule_version=AI_REFERRAL_RULE_VERSION,
        analyzer_version=ANALYZER_VERSION,
    )
    session.add(classification)
    await session.flush()
    return classification


async def seed_visibility_snapshot(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    completed_at: datetime,
    visibility_score: float,
    total_completed: int = 3,
    per_engine: dict[str, float] | None = None,
    status: str = AUDIT_STATUS_COMPLETED,
) -> MetricSnapshot:
    """Seed one dashboard-status audit + its folded ``MetricSnapshot``.

    For the A8/A9 visibility-series fixtures: ``per_engine`` maps
    ``logical_engine -> brand_mention_rate`` and is stored in the aggregate
    ``metrics["per_engine"]`` block exactly like the run-level finalize
    writes it (the snapshot builder reads rates from there).
    """
    audit = Audit(
        workspace_id=workspace_id,
        project_id=project_id,
        status=status,
        completed_at=completed_at,
    )
    session.add(audit)
    await session.flush()
    snapshot = MetricSnapshot(
        workspace_id=workspace_id,
        audit_id=audit.id,
        project_id=project_id,
        analyzer_version=ANALYZER_VERSION,
        scoring_rule_version=SCORING_RULE_VERSION,
        total_completed=total_completed,
        visibility_score=visibility_score,
        metrics={
            "brand_mention_rate": round(visibility_score / 100.0, 4),
            "per_engine": {
                engine: {"brand_mention_rate": rate}
                for engine, rate in (per_engine or {}).items()
            },
        },
        source_analysis_ids=[],
        source_artifact_ids=[],
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def seed_theme_analysis(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    audit_id: uuid.UUID,
    prompt_index: int,
    theme: str,
    intent: str,
    logical_engine: str = ENGINE_GEMINI,
    brand_mentioned: bool = False,
    competitors_mentioned: list[str] | None = None,
    repetition: int = 0,
) -> ResponseAnalysis:
    """Seed one per-execution analysis under its frozen prompt axes.

    Builds the minimal chain the theme rollup joins over:
    ``AuditPromptSnapshot`` (frozen theme/intent) -> ``AuditEngineSnapshot``
    -> ``AuditTask`` -> ``ResponseAnalysis`` carrying
    ``(audit_id, prompt_index)``. ``competitors_mentioned`` lands in the
    persisted ``score`` dict exactly like the deterministic scorer writes it.
    """
    prompt_snapshot = AuditPromptSnapshot(
        audit_id=audit_id,
        prompt_index=prompt_index,
        text=f"frozen prompt {prompt_index}",
        theme=theme,
        intent=intent,
    )
    session.add(prompt_snapshot)
    await session.flush()
    # One engine snapshot per (audit, engine) — reuse it across analyses.
    engine_snapshot = await session.scalar(
        select(AuditEngineSnapshot).where(
            AuditEngineSnapshot.audit_id == audit_id,
            AuditEngineSnapshot.logical_engine == logical_engine,
        )
    )
    if engine_snapshot is None:
        engine_snapshot = AuditEngineSnapshot(
            audit_id=audit_id,
            logical_engine=logical_engine,
            transport_provider="google",
            transport_model="gemini-flash-latest",
        )
        session.add(engine_snapshot)
        await session.flush()
    task = AuditTask(
        audit_id=audit_id,
        workspace_id=workspace_id,
        prompt_snapshot_id=prompt_snapshot.id,
        engine_snapshot_id=engine_snapshot.id,
        prompt_index=prompt_index,
        repetition=repetition,
        logical_engine=logical_engine,
        transport_provider="google",
        transport_model="gemini-flash-latest",
        idempotency_key=uuid.uuid4().hex,
    )
    session.add(task)
    await session.flush()
    analysis = ResponseAnalysis(
        workspace_id=workspace_id,
        audit_id=audit_id,
        task_id=task.id,
        analyzer_version=ANALYZER_VERSION,
        scoring_rule_version=SCORING_RULE_VERSION,
        logical_engine=logical_engine,
        prompt_index=prompt_index,
        repetition=repetition,
        brand_mentioned=brand_mentioned,
        score={"competitors_mentioned": list(competitors_mentioned or [])},
    )
    session.add(analysis)
    await session.flush()
    return analysis
