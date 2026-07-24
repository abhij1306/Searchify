#!/usr/bin/env python3
"""Seed the Searchify dev DB with a full integrations + analytics fixture graph.

Creates, for the seeded demo workspace (demo@searchify.dev) and its
"Acme Running Shoes" project:

  - Google OAuth grant (stub-encrypted dummy tokens) + GSC & GA4 connections
  - Microsoft OAuth grant + Bing connection
  - 3 ACTIVE property mappings (GSC/Bing property_ref == connection.account_ref;
    GA4 keeps the provider's ``properties/`` account_ref spelling on the
    connection but maps + writes metric rows under the canonical bare id,
    mirroring the normalizing create/derive paths)
  - Terminal (succeeded) sync runs + immutable import artifacts per dataset
  - IntegrationMetricRow history 2026-07-08..2026-07-21 (14 days):
    55 GSC pages + 55 GSC queries per day (keyset-paging volume), GA4
    channel/source-medium/referrer/landing rows with AI-referral sources,
    2 days of Bing rows (not Traffic-consumed — exclusion control)
  - 10 completed audits + MetricSnapshots on 10 distinct days (07-08..07-17)
    with per-engine rates (visibility axis for the correlation; W_SHORT
    07-19..07-21 deliberately has none -> insufficient_data)
  - Theme analyses (AuditPromptSnapshot -> AuditEngineSnapshot -> AuditTask
    -> ResponseAnalysis) for the themes rollup
  - "Empty Co (no integrations)" project with no integration data

Then drives the REAL C5 chain: enqueue_post_sync_projections() over all
artifact ids + AnalyticsWorker.run_until_idle(), plus explicit windowed
refreshes for W_SHORT (2026-07-19..2026-07-21).

Idempotent: if the Google grant already exists for the workspace, the seed
phase is skipped (chain is NOT re-run). Run from backend/:

  cd <repo-root>/backend
  uv run python testing/local-stack/seed_integrations.py
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select

from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION
from app.core.config.integrations import (
    DATASET_BING_PAGE_DAILY,
    DATASET_BING_QUERY_DAILY,
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    GRANT_STATUS_CONNECTED,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_PROVIDER_BING,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
    INTEGRATION_TRANSPORT_GOOGLE,
    INTEGRATION_TRANSPORT_MICROSOFT,
    MAPPING_STATUS_ACTIVE,
    SYNC_KIND_SCHEDULED,
    normalize_ga4_property_ref,
    pack_dimension_key,
)
from app.core.config.provider_catalog import (
    ENGINE_CHATGPT,
    ENGINE_CLAUDE,
    ENGINE_GEMINI,
)
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
from app.core.database import SessionLocal
from app.core.security import encrypt_secret
from app.domain.analytics.enqueue import (
    enqueue_analytics_snapshot_refresh,
    enqueue_post_sync_projections,
    enqueue_traffic_snapshot_refresh,
)
from app.models.analysis import MetricSnapshot, ResponseAnalysis
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
    IntegrationPropertyMapping,
    IntegrationSyncRun,
)
from app.models.project import Project
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.workers.analytics_worker import AnalyticsWorker

W_FULL = (date(2026, 7, 8), date(2026, 7, 21))
W_SHORT = (date(2026, 7, 19), date(2026, 7, 21))
DAYS = [W_FULL[0] + timedelta(days=i) for i in range(14)]
VISIBILITY_DAYS = [date(2026, 7, 8) + timedelta(days=i) for i in range(10)]

GSC_PROPERTY = "sc-domain:acme-running.example.com"
# GA4 property refs are numeric property ids (the API validates the shape;
# never domain-shaped). The connection's account_ref keeps the provider's
# resource-name spelling (what Google's account listing returns) while
# mappings + metric rows use the CANONICAL bare id — exactly what the
# normalizing write/derive paths produce.
GA4_PROPERTY = "properties/123456789"
GA4_PROPERTY_CANONICAL = normalize_ga4_property_ref(GA4_PROPERTY)
BING_PROPERTY = "https://acme-running.example.com"

GSC_PAGES = [
    "https://acme-running.example.com/",
    "https://acme-running.example.com/pricing",
    "https://acme-running.example.com/products/trail-racer",
    "https://acme-running.example.com/products/road-glide",
    "https://acme-running.example.com/products/cloud-stride",
    "https://blog.acme-running.example.com/cushioning-guide",
] + [
    f"https://acme-running.example.com/blog/post-{i:02d}" for i in range(1, 50)
]  # 55 pages total
GSC_QUERIES = [
    "best running shoes",
    "acme trail racer review",
    "lightweight road running shoes",
    "running shoes for flat feet",
    "acme vs velocity sports",
    "marathon training shoes",
] + [f"running shoe query {i:02d}" for i in range(1, 50)]  # 55 queries total

GA4_CHANNELS = ["Organic Search", "Referral", "Paid Search"]
GA4_SOURCE_MEDIUM = [
    ("google", "organic"),
    ("bing", "organic"),
    ("chatgpt.com", "referral"),
    ("perplexity.ai", "referral"),
]
GA4_REFERRERS = [
    "https://chatgpt.com/",
    "https://gemini.google.com/app",
    "https://claude.ai/",
    "https://perplexity.ai/",
    "https://copilot.microsoft.com/",
    "https://news.ycombinator.com/",  # non-AI control
]
GA4_LANDING = [
    ("/", "google", "organic"),
    ("/pricing", "google", "organic"),
    ("/products/trail-racer", "chatgpt.com", "referral"),
    ("/blog/best-running-shoes-2026", "perplexity.ai", "referral"),
]

BING_PAGES = GSC_PAGES[:4]
BING_QUERIES = GSC_QUERIES[:4]


def _ga4_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _gsc_metrics(clicks: int, impressions: int, position: float) -> dict:
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions, 4),
        "position": round(position, 1),
    }


def _ga4_metrics(sessions: int) -> dict:
    return {
        "sessions": sessions,
        "engagedSessions": max(sessions - 3, 0),
        "conversions": sessions % 4,
    }


async def _demo_workspace_project(session):
    user = await session.scalar(select(User).where(User.email == "demo@searchify.dev"))
    if user is None:
        raise SystemExit("demo user missing — run testing/local-stack/seed.sh first")
    workspace = await session.scalar(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at.asc())
        .limit(1)
    )
    project = await session.scalar(
        select(Project).where(
            Project.workspace_id == workspace.id,
            Project.name == "Acme Running Shoes",
        )
    )
    if project is None:
        raise SystemExit("Acme project missing — run seed.sh first")
    return workspace, project


async def _empty_project(session, workspace_id) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.workspace_id == workspace_id,
            Project.name == "Empty Co (no integrations)",
        )
    )
    if project is None:
        project = Project(workspace_id=workspace_id, name="Empty Co (no integrations)")
        session.add(project)
        await session.flush()
    return project


def _grant(workspace_id, transport, scopes) -> IntegrationOAuthGrant:
    return IntegrationOAuthGrant(
        workspace_id=workspace_id,
        transport=transport,
        access_token_encrypted=encrypt_secret("stub-access-token"),
        refresh_token_encrypted=encrypt_secret("stub-refresh-token"),
        token_expires_at=datetime.now(UTC) + timedelta(seconds=3600),
        granted_scopes=list(scopes),
        status=GRANT_STATUS_CONNECTED,
    )


def _connection(workspace_id, grant_id, provider, account_ref) -> IntegrationConnection:
    return IntegrationConnection(
        workspace_id=workspace_id,
        grant_id=grant_id,
        provider=provider,
        label=f"{provider.upper()} — Acme Running Shoes",
        account_ref=account_ref,
        last_synced_at=datetime(2026, 7, 21, 6, 0, tzinfo=UTC),
    )


def _terminal_run(workspace_id, connection_id, window, sync_kind=SYNC_KIND_SCHEDULED):
    return IntegrationSyncRun(
        workspace_id=workspace_id,
        connection_id=connection_id,
        sync_kind=sync_kind,
        window_start=window[0],
        window_end=window[1],
        resync_seq=0,
        idempotency_key=uuid.uuid4().hex,
        status=TASK_STATUS_SUCCEEDED,
        completed_at=datetime(2026, 7, 21, 6, 30, tzinfo=UTC),
    )


def _artifact(workspace_id, run, connection, provider, dataset) -> IntegrationImportArtifact:
    template = INTEGRATION_DATASET_TEMPLATES[dataset]
    payload = {"rows": []}
    return IntegrationImportArtifact(
        workspace_id=workspace_id,
        sync_run_id=run.id,
        connection_id=connection.id,
        provider=provider,
        dataset=dataset,
        query_snapshot={
            "dimensions": list(template.dimensions),
            "metrics": list(template.metrics),
        },
        payload_hash=hashlib.sha256(repr(payload).encode()).hexdigest(),
        row_count=0,
        payload=payload,
    )


def _row(workspace_id, project_id, property_ref, provider, dataset, day, dims, metrics, artifact_id):
    return IntegrationMetricRow(
        workspace_id=workspace_id,
        project_id=project_id,
        property_ref=property_ref,
        provider=provider,
        dataset=dataset,
        date=day,
        dimension_key=pack_dimension_key(dims),
        metrics=metrics,
        source_artifact_id=artifact_id,
        resync_seq=0,
    )


async def seed() -> dict:
    async with SessionLocal() as session:
        workspace, project = await _demo_workspace_project(session)
        existing = await session.scalar(
            select(IntegrationOAuthGrant).where(
                IntegrationOAuthGrant.workspace_id == workspace.id,
                IntegrationOAuthGrant.transport == INTEGRATION_TRANSPORT_GOOGLE,
            )
        )
        if existing is not None:
            return {"skipped": True, "reason": "google grant already seeded"}
        empty_project = await _empty_project(session, workspace.id)

        google = _grant(
            workspace.id,
            INTEGRATION_TRANSPORT_GOOGLE,
            [
                "https://www.googleapis.com/auth/webmasters.readonly",
                "https://www.googleapis.com/auth/analytics.readonly",
            ],
        )
        microsoft = _grant(
            workspace.id,
            INTEGRATION_TRANSPORT_MICROSOFT,
            ["offline_access", "https://webmaster.bing.com/api/webmaster.manage"],
        )
        session.add_all([google, microsoft])
        await session.flush()
        gsc = _connection(workspace.id, google.id, INTEGRATION_PROVIDER_GSC, GSC_PROPERTY)
        ga4 = _connection(workspace.id, google.id, INTEGRATION_PROVIDER_GA4, GA4_PROPERTY)
        bing = _connection(workspace.id, microsoft.id, INTEGRATION_PROVIDER_BING, BING_PROPERTY)
        session.add_all([gsc, ga4, bing])
        await session.flush()
        for connection, provider in ((gsc, "gsc"), (ga4, "ga4"), (bing, "bing")):
            session.add(
                IntegrationPropertyMapping(
                    workspace_id=workspace.id,
                    connection_id=connection.id,
                    provider=provider,
                    # GA4 mappings persist the CANONICAL bare numeric id
                    # (create_mapping normalizes); the connection keeps the
                    # provider's resource-name account_ref spelling.
                    property_ref=(
                        GA4_PROPERTY_CANONICAL
                        if provider == "ga4"
                        else connection.account_ref
                    ),
                    project_id=project.id,
                    status=MAPPING_STATUS_ACTIVE,
                )
            )

        # Terminal runs + artifacts (one run per connection over W_FULL).
        runs = {c.provider: _terminal_run(workspace.id, c.id, W_FULL) for c in (gsc, ga4, bing)}
        session.add_all(runs.values())
        await session.flush()
        artifacts: dict[str, IntegrationImportArtifact] = {}
        for connection, datasets in (
            (gsc, (DATASET_GSC_PAGE_DAILY, DATASET_GSC_QUERY_DAILY)),
            (
                ga4,
                (
                    DATASET_GA4_CHANNEL_DAILY,
                    DATASET_GA4_SOURCE_MEDIUM_DAILY,
                    DATASET_GA4_REFERRER_DAILY,
                    DATASET_GA4_LANDING_DAILY,
                ),
            ),
            (bing, (DATASET_BING_PAGE_DAILY, DATASET_BING_QUERY_DAILY)),
        ):
            run = runs[connection.provider]
            for dataset in datasets:
                artifact = _artifact(
                    workspace.id, run, connection, connection.provider, dataset
                )
                session.add(artifact)
                await session.flush()
                artifacts[dataset] = artifact

        # --- Metric rows ------------------------------------------------------
        rows: list[IntegrationMetricRow] = []
        for di, day in enumerate(DAYS):
            for pi, page in enumerate(GSC_PAGES):
                impressions = 100 + 5 * pi + 2 * di
                clicks = (pi + di) % 9 + 1
                rows.append(
                    _row(
                        workspace.id, project.id, GSC_PROPERTY, "gsc",
                        DATASET_GSC_PAGE_DAILY, day, [page, day.isoformat()],
                        _gsc_metrics(clicks, impressions, 2.0 + 0.3 * pi),
                        artifacts[DATASET_GSC_PAGE_DAILY].id,
                    )
                )
            for qi, query in enumerate(GSC_QUERIES):
                impressions = 80 + 4 * qi + 3 * di
                clicks = (2 * qi + di) % 7 + 1
                rows.append(
                    _row(
                        workspace.id, project.id, GSC_PROPERTY, "gsc",
                        DATASET_GSC_QUERY_DAILY, day, [query, day.isoformat()],
                        _gsc_metrics(clicks, impressions, 3.0 + 0.2 * qi),
                        artifacts[DATASET_GSC_QUERY_DAILY].id,
                    )
                )
            for ci, channel in enumerate(GA4_CHANNELS):
                sessions = {"Organic Search": 40 + 2 * di, "Referral": 15 + di,
                            "Paid Search": 10}[channel]
                rows.append(
                    _row(
                        workspace.id, project.id, GA4_PROPERTY_CANONICAL, "ga4",
                        DATASET_GA4_CHANNEL_DAILY, day,
                        [channel, _ga4_date(day)], _ga4_metrics(sessions),
                        artifacts[DATASET_GA4_CHANNEL_DAILY].id,
                    )
                )
            for source, medium in GA4_SOURCE_MEDIUM:
                sessions = {
                    ("google", "organic"): 35 + 2 * di,
                    ("bing", "organic"): 8,
                    ("chatgpt.com", "referral"): 6 + di,
                    ("perplexity.ai", "referral"): 3 + (di % 3),
                }[(source, medium)]
                rows.append(
                    _row(
                        workspace.id, project.id, GA4_PROPERTY_CANONICAL, "ga4",
                        DATASET_GA4_SOURCE_MEDIUM_DAILY, day,
                        [source, medium, _ga4_date(day)], _ga4_metrics(sessions),
                        artifacts[DATASET_GA4_SOURCE_MEDIUM_DAILY].id,
                    )
                )
            for ri, referrer in enumerate(GA4_REFERRERS):
                sessions = [6 + di, 2 + (di % 2), 1 + (di % 3 == 0),
                            3 + (di % 3), 1, 4][ri]
                rows.append(
                    _row(
                        workspace.id, project.id, GA4_PROPERTY_CANONICAL, "ga4",
                        DATASET_GA4_REFERRER_DAILY, day,
                        [referrer, _ga4_date(day)], _ga4_metrics(sessions),
                        artifacts[DATASET_GA4_REFERRER_DAILY].id,
                    )
                )
            for li, (page, source, medium) in enumerate(GA4_LANDING):
                sessions = [30 + di, 12, 5 + di, 2 + (di % 3)][li]
                rows.append(
                    _row(
                        workspace.id, project.id, GA4_PROPERTY_CANONICAL, "ga4",
                        DATASET_GA4_LANDING_DAILY, day,
                        [page, source, medium, _ga4_date(day)], _ga4_metrics(sessions),
                        artifacts[DATASET_GA4_LANDING_DAILY].id,
                    )
                )
        for day in (date(2026, 7, 20), date(2026, 7, 21)):  # Bing: 2 control days
            for bi, page in enumerate(BING_PAGES):
                rows.append(
                    _row(
                        workspace.id, project.id, BING_PROPERTY, "bing",
                        DATASET_BING_PAGE_DAILY, day, [page, day.isoformat()],
                        {"clicks": 2 + bi, "impressions": 30 + 5 * bi},
                        artifacts[DATASET_BING_PAGE_DAILY].id,
                    )
                )
            for bi, query in enumerate(BING_QUERIES):
                rows.append(
                    _row(
                        workspace.id, project.id, BING_PROPERTY, "bing",
                        DATASET_BING_QUERY_DAILY, day, [query, day.isoformat()],
                        {"clicks": 1 + bi, "impressions": 20 + 4 * bi},
                        artifacts[DATASET_BING_QUERY_DAILY].id,
                    )
                )
        session.add_all(rows)
        await session.flush()

        # --- Visibility snapshots (10 distinct days) + theme analyses ---------
        for di, day in enumerate(VISIBILITY_DAYS):
            completed_at = datetime(day.year, day.month, day.day, 18, 0, tzinfo=UTC)
            audit = Audit(
                workspace_id=workspace.id,
                project_id=project.id,
                status="completed",
                completed_at=completed_at,
            )
            session.add(audit)
            await session.flush()
            visibility = 52.0 + 3.1 * di  # rising axis (positive correlation)
            session.add(
                MetricSnapshot(
                    workspace_id=workspace.id,
                    audit_id=audit.id,
                    project_id=project.id,
                    analyzer_version=ANALYZER_VERSION,
                    scoring_rule_version=SCORING_RULE_VERSION,
                    total_completed=6,
                    visibility_score=round(visibility, 2),
                    metrics={
                        "brand_mention_rate": round(visibility / 100.0, 4),
                        "per_engine": {
                            ENGINE_GEMINI: {"brand_mention_rate": round(0.40 + 0.02 * di, 4)},
                            ENGINE_CHATGPT: {"brand_mention_rate": round(0.55 + 0.03 * di, 4)},
                            ENGINE_CLAUDE: {"brand_mention_rate": round(0.45 + 0.015 * di, 4)},
                        },
                    },
                    source_analysis_ids=[],
                    source_artifact_ids=[],
                )
            )
            if di < 3:  # theme rollup fixtures on the first three audits
                for prompt_index, (theme, intent, mentioned) in enumerate(
                    [("Sizing", "discovery", True), ("Pricing", "purchase", di % 2 == 0)]
                ):
                    prompt_snapshot = AuditPromptSnapshot(
                        audit_id=audit.id,
                        prompt_index=prompt_index,
                        text=f"seeded prompt {prompt_index}",
                        theme=theme,
                        intent=intent,
                    )
                    session.add(prompt_snapshot)
                    await session.flush()
                    engine_snapshot = await session.scalar(
                        select(AuditEngineSnapshot).where(
                            AuditEngineSnapshot.audit_id == audit.id,
                            AuditEngineSnapshot.logical_engine == ENGINE_GEMINI,
                        )
                    )
                    if engine_snapshot is None:
                        engine_snapshot = AuditEngineSnapshot(
                            audit_id=audit.id,
                            logical_engine=ENGINE_GEMINI,
                            transport_provider="google",
                            transport_model="gemini-flash-latest",
                        )
                        session.add(engine_snapshot)
                        await session.flush()
                    task = AuditTask(
                        audit_id=audit.id,
                        workspace_id=workspace.id,
                        prompt_snapshot_id=prompt_snapshot.id,
                        engine_snapshot_id=engine_snapshot.id,
                        prompt_index=prompt_index,
                        repetition=0,
                        logical_engine=ENGINE_GEMINI,
                        transport_provider="google",
                        transport_model="gemini-flash-latest",
                        idempotency_key=uuid.uuid4().hex,
                    )
                    session.add(task)
                    await session.flush()
                    session.add(
                        ResponseAnalysis(
                            workspace_id=workspace.id,
                            audit_id=audit.id,
                            task_id=task.id,
                            analyzer_version=ANALYZER_VERSION,
                            scoring_rule_version=SCORING_RULE_VERSION,
                            logical_engine=ENGINE_GEMINI,
                            prompt_index=prompt_index,
                            repetition=0,
                            brand_mentioned=mentioned,
                            score={"competitors_mentioned": ["Velocity Sports"] if mentioned else []},
                        )
                    )
        await session.commit()
        return {
            "skipped": False,
            "workspace_id": str(workspace.id),
            "project_id": str(project.id),
            "empty_project_id": str(empty_project.id),
            "gsc_connection_id": str(gsc.id),
            "ga4_connection_id": str(ga4.id),
            "bing_connection_id": str(bing.id),
            "artifact_ids": {dataset: str(a.id) for dataset, a in artifacts.items()},
            "metric_rows": len(rows),
        }


async def drive_chain(artifact_ids: list[str], project_id: str) -> dict:
    async with SessionLocal() as session:
        workspace, project = await _demo_workspace_project(session)
        enqueued = await enqueue_post_sync_projections(
            session,
            project_id=project.id,
            import_artifact_ids=[uuid.UUID(a) for a in artifact_ids],
        )
        await session.commit()
    worker = AnalyticsWorker(session_factory=SessionLocal, owner="seed-driver")
    drained = await worker.run_until_idle()
    async with SessionLocal() as session:
        workspace, project = await _demo_workspace_project(session)
        await enqueue_traffic_snapshot_refresh(
            session,
            workspace_id=workspace.id,
            project_id=project.id,
            window_start=W_SHORT[0],
            window_end=W_SHORT[1],
            resync_seq=0,
        )
        await enqueue_analytics_snapshot_refresh(
            session,
            workspace_id=workspace.id,
            project_id=project.id,
            window_start=W_SHORT[0],
            window_end=W_SHORT[1],
            resync_seq=0,
        )
        await session.commit()
    drained += await worker.run_until_idle()
    async with SessionLocal() as session:
        from app.models.analytics import ReferralClassification, ReferralEvent, AnalyticsSnapshot
        from app.models.traffic import TrafficSnapshot
        counts = {
            "referral_events": await session.scalar(select(func.count(ReferralEvent.id))),
            "referral_classifications": await session.scalar(
                select(func.count(ReferralClassification.id))
            ),
            "analytics_snapshots": await session.scalar(select(func.count(AnalyticsSnapshot.id))),
            "traffic_snapshots": await session.scalar(select(func.count(TrafficSnapshot.id))),
        }
    return {"enqueued": len(enqueued), "tasks_drained": drained, **counts}


async def main() -> None:
    summary = await seed()
    print("SEED:", summary, flush=True)
    if summary.get("skipped"):
        return
    chain = await drive_chain(
        list(summary["artifact_ids"].values()), summary["project_id"]
    )
    print("CHAIN:", chain, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
