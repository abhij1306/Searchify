# Referral ingest projection (A5): the ``ingest_referrals`` executor.
#
# The first link of the C5 referral chain. For the payload's
# ``import_artifact_id`` it reads the artifact's LATEST-``resync_seq``
# ``IntegrationMetricRow`` rows whose dataset is one of the C1 GA4
# referral-dimension datasets (``TRAFFIC_GA4_REFERRAL_DATASETS``), maps each
# row's packed ``dimension_key`` into referral signals, runs the A4
# deterministic sanitize pass BEFORE the write (invariant 6), and inserts
# immutable ``ReferralEvent`` rows via ``ON CONFLICT DO NOTHING`` on
# ``(import_id, content_hash)`` — a re-run is a dedup no-op, never an
# overwrite (invariant 3). On completion it enqueues ``classify_referrals``
# for the artifact (the chain's next link).
#
# Pure projection over persisted rows (invariant 7): NO provider fetch, no
# network I/O, and this module is the single writer of ``ReferralEvent``.
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.core.config.analytics import REFERRAL_SANITIZE_VERSION
from app.core.config.integrations import (
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DIMENSION_KEY_SEPARATOR,
    INTEGRATION_DATASET_TEMPLATES,
)
from app.core.config.traffic import TRAFFIC_GA4_REFERRAL_DATASETS
from app.domain.analytics.enqueue import enqueue_classify_referrals
from app.domain.analytics.sanitize import (
    SanitizedReferral,
    sanitize_raw_payload,
    sanitize_referral,
)
from app.models.analytics import AnalyticsTask, ReferralEvent
from app.models.integrations import IntegrationImportArtifact, IntegrationMetricRow


@dataclass(frozen=True)
class ReferralSignals:
    """The pre-sanitize referral signals mapped from one metric row.

    GA4 referral-dimension rows are session AGGREGATES: they carry no
    landing page, user-agent, or session identity, so only the
    referrer / UTM signals the dataset's dimensions express are set.
    """

    landing_url: str
    referrer_url: str
    utm_source: str
    utm_medium: str
    utm_campaign: str


def _clean(value: str) -> str:
    return value.strip()


def _signals_for_row(row: IntegrationMetricRow) -> ReferralSignals | None:
    """Map one metric row's packed ``dimension_key`` into referral signals.

    The key packs the dataset template's declared dimension values in order
    (contract C1). Splitting from the RIGHT peels the always-trailing
    ``date`` dimension (its value also lives on ``row.date``) without
    breaking on a ``" | "`` inside a free-form leading value such as a
    ``fullReferrer`` URL. A row whose key does not unpack into the
    template's declared arity is un-mappable and skipped (never guessed).
    """
    template = INTEGRATION_DATASET_TEMPLATES.get(row.dataset)
    if template is None:
        return None
    parts = row.dimension_key.rsplit(
        DIMENSION_KEY_SEPARATOR, len(template.dimensions) - 1
    )
    if len(parts) != len(template.dimensions):
        return None
    *dimension_values, _date_value = parts
    if row.dataset == DATASET_GA4_REFERRER_DAILY:
        # Dimensions: (fullReferrer, date) — the full referring URL.
        (full_referrer,) = dimension_values
        return ReferralSignals(
            landing_url="",
            referrer_url=_clean(full_referrer),
            utm_source="",
            utm_medium="",
            utm_campaign="",
        )
    if row.dataset == DATASET_GA4_SOURCE_MEDIUM_DAILY:
        # Dimensions: (sessionSource, sessionMedium, date) — the session's
        # traffic-source tags (GA4's sessionSource is the utm_source tag
        # when tagged, else the referring source).
        session_source, session_medium = dimension_values
        return ReferralSignals(
            landing_url="",
            referrer_url="",
            utm_source=_clean(session_source),
            utm_medium=_clean(session_medium),
            utm_campaign="",
        )
    return None


def _sanitize_signals(
    row: IntegrationMetricRow, signals: ReferralSignals
) -> SanitizedReferral:
    """Run the A4 redaction pass over one row's signals (pre-write, inv. 6).

    GA4 aggregate rows carry no user-agent or raw session id, so those
    sanitize to empty tokens. The persisted ``raw`` is the allowlist-
    filtered trace payload (dataset / date / dimension_key + the marketing
    signals), built from the SANITIZED referrer host so nothing the
    redaction pass would drop leaks through ``raw``.
    """
    base = sanitize_referral(
        landing_url=signals.landing_url,
        referrer_url=signals.referrer_url,
        user_agent=None,
        session_id=None,
        raw=None,
    )
    raw_candidate: dict[str, object] = {
        "dataset": row.dataset,
        "date": row.date.isoformat(),
        "dimension_key": row.dimension_key,
    }
    if base.referrer_host:
        raw_candidate["referrer_host"] = base.referrer_host
    if signals.utm_source:
        raw_candidate["utm_source"] = signals.utm_source
    if signals.utm_medium:
        raw_candidate["utm_medium"] = signals.utm_medium
    if signals.utm_campaign:
        raw_candidate["utm_campaign"] = signals.utm_campaign
    return replace(base, raw=sanitize_raw_payload(raw_candidate))


def _content_hash(
    row: IntegrationMetricRow,
    signals: ReferralSignals,
    sanitized: SanitizedReferral,
) -> str:
    """Deterministic dedupe key over the row identity + sanitized signals.

    Stable across executor re-runs (the artifact is immutable), so the
    ``(import_id, content_hash)`` conflict target makes a re-run a no-op.
    """
    canonical = json.dumps(
        {
            "dataset": row.dataset,
            "date": row.date.isoformat(),
            "dimension_key": row.dimension_key,
            "landing_url": sanitized.landing_url,
            "referrer_url": sanitized.referrer_url,
            "referrer_host": sanitized.referrer_host,
            "utm_source": signals.utm_source,
            "utm_medium": signals.utm_medium,
            "utm_campaign": signals.utm_campaign,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_event_values(
    task: AnalyticsTask,
    artifact: IntegrationImportArtifact,
    row: IntegrationMetricRow,
) -> dict[str, Any] | None:
    """Build the ``ReferralEvent`` column values for one metric row.

    Returns ``None`` for a row that does not map to referral signals. The
    event takes the chain's workspace/project identity from the claimed
    task (the enqueue hook resolved the artifact in this workspace); the
    exact source row stays linked via ``source_metric_row_id`` either way
    (invariant 4).
    """
    signals = _signals_for_row(row)
    if signals is None:
        return None
    sanitized = _sanitize_signals(row, signals)
    return {
        "workspace_id": task.workspace_id,
        "project_id": task.project_id,
        "source": artifact.provider,
        "import_id": artifact.id,
        "source_metric_row_id": row.id,
        # Provider data is date-grained: the UTC instant of the row's date.
        "occurred_at": datetime.combine(row.date, time.min, tzinfo=UTC),
        "landing_url": sanitized.landing_url,
        "referrer_host": sanitized.referrer_host,
        "referrer_url": sanitized.referrer_url,
        "utm_source": signals.utm_source,
        "utm_medium": signals.utm_medium,
        "utm_campaign": signals.utm_campaign,
        "user_agent": sanitized.user_agent,
        "session_id_hash": sanitized.session_id_hash,
        "raw": sanitized.raw,
        "content_hash": _content_hash(row, signals, sanitized),
        "sanitize_version": REFERRAL_SANITIZE_VERSION,
    }


async def _latest_referral_rows(
    session: AsyncSession, artifact: IntegrationImportArtifact
) -> list[IntegrationMetricRow]:
    """The artifact's referral-dataset rows at the latest ``resync_seq``.

    Consumers read the LATEST revision per identity tuple
    ``(project_id, property_ref, provider, dataset, date, dimension_key)``
    (the ``uq_integration_metric_row_identity`` columns): a row superseded
    by a later re-sync at a higher ``resync_seq`` is stale evidence and is
    never ingested, even if this artifact's queued task runs late.
    """
    newer_rows = aliased(IntegrationMetricRow)
    newer_exists = (
        select(newer_rows.id)
        .where(newer_rows.project_id == IntegrationMetricRow.project_id)
        .where(newer_rows.property_ref == IntegrationMetricRow.property_ref)
        .where(newer_rows.provider == IntegrationMetricRow.provider)
        .where(newer_rows.dataset == IntegrationMetricRow.dataset)
        .where(newer_rows.date == IntegrationMetricRow.date)
        .where(newer_rows.dimension_key == IntegrationMetricRow.dimension_key)
        .where(newer_rows.resync_seq > IntegrationMetricRow.resync_seq)
        .exists()
    )
    stmt = (
        select(IntegrationMetricRow)
        .where(IntegrationMetricRow.source_artifact_id == artifact.id)
        .where(IntegrationMetricRow.dataset.in_(sorted(TRAFFIC_GA4_REFERRAL_DATASETS)))
        .where(~newer_exists)
        .order_by(IntegrationMetricRow.date.asc(), IntegrationMetricRow.id.asc())
    )
    return list((await session.scalars(stmt)).all())


def _payload_artifact_id(task: AnalyticsTask) -> uuid.UUID:
    raw = (task.payload or {}).get("import_artifact_id")
    if not raw:
        raise ValueError("ingest_referrals payload missing import_artifact_id")
    return uuid.UUID(str(raw))


async def ingest_referrals(
    session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
) -> None:
    """``ingest_referrals`` executor: project one artifact's referral rows.

    One transaction: read the latest-``resync_seq`` referral-dataset metric
    rows, sanitize + insert the dedup-safe immutable events, then enqueue
    ``classify_referrals`` for the artifact (C5 chain). Idempotent: a
    re-run conflicts on ``(import_id, content_hash)`` and inserts nothing,
    and the chained enqueue dedupes on its deterministic idempotency key.
    """
    if task.project_id is None:
        raise ValueError("ingest_referrals task missing project_id")
    artifact_id = _payload_artifact_id(task)
    async with session_factory() as session:
        artifact = await session.get(IntegrationImportArtifact, artifact_id)
        # Never project rows for an artifact outside the claimed task's
        # workspace (invariant 5); an unknown id fails the attempt loud.
        if artifact is None or artifact.workspace_id != task.workspace_id:
            raise ValueError(f"unknown import artifact: {artifact_id}")

        values: list[dict[str, Any]] = []
        for row in await _latest_referral_rows(session, artifact):
            event_values = _build_event_values(task, artifact, row)
            if event_values is not None:
                values.append(event_values)
        if values:
            stmt = (
                pg_insert(ReferralEvent)
                .values(values)
                .on_conflict_do_nothing(
                    index_elements=["import_id", "content_hash"]
                )
            )
            await session.execute(stmt)

        await enqueue_classify_referrals(
            session,
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            import_artifact_id=artifact.id,
        )
        await session.commit()
