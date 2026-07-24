"""Direct-ORM audit seeding for Searchify demo data.

Called by seed.sh (PROJECT_ID env var set) after the project/prompts/provider
connections exist via the API. Creates 4 audits against that project, one per
lifecycle status family, WITHOUT calling any real provider (no BYOK keys are
available in this sandbox):

  1. "Weekly Benchmark (completed)"        -- status=completed, full pipeline:
     prompt/engine snapshots, 1 task per (prompt x engine) succeeded, raw
     artifacts, provider attempts, response analyses (brand/competitor
     mentions + citations covering owned/unintended/competitor/third_party),
     and a MetricSnapshot.
  2. "Partial Outage Run (partially_completed)" -- some tasks succeeded
     (with full analysis), some tasks failed (rate_limit/timeout), status
     partially_completed, MetricSnapshot reflects only the succeeded subset.
  3. "Auth Failure Run (failed)"            -- every task failed
     (auth_failure), status=failed, no MetricSnapshot (nothing to aggregate).
  4. "Live Benchmark (running)"             -- tasks queued/running, no
     results yet, status=running -- represents a run "currently in flight".

Idempotent: looks up each audit by its unique title stashed in
Audit.system_instruction's trailing marker; if all 4 already exist for the
project, this script is a no-op.
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.models.audit import (
    Audit,
    AuditEngineSnapshot,
    AuditEvent,
    AuditPromptSnapshot,
    AuditTask,
    ProviderAttempt,
    RawResponseArtifact,
)
from app.models.analysis import (
    BrandMention,
    Citation,
    CompetitorMention,
    MetricSnapshot,
    ResponseAnalysis,
)
from app.models.project import Project
from app.models.prompt import Prompt, PromptSet
from app.models.provider import ProviderConnection
from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION

PROJECT_ID = uuid.UUID(os.environ["PROJECT_ID"])

ENGINES = [
    ("chatgpt", "openai", "gpt-5.4"),
    ("claude", "anthropic", "claude-sonnet-4-6"),
    ("gemini", "google", "gemini-flash-latest"),
]

SAMPLE_ANSWERS = [
    (
        "When it comes to marathon training, Acme is frequently recommended "
        "alongside Velocity Sports for its cushioning. Many runners also "
        "mention Trailblazer Co for trail conditions. You can read more at "
        "https://acme-running.example.com/marathon-guide and "
        "https://velocitysports.example.com/reviews.",
        ["https://acme-running.example.com/marathon-guide", "https://velocitysports.example.com/reviews"],
    ),
    (
        "Acme running shoes are known for durable outsoles, while Nimbus "
        "Athletics focuses on lightweight builds. See the comparison at "
        "https://blog.acme-running.example.com/vs-nimbus and a third-party "
        "roundup at https://runnersworld-example.com/best-shoes-2026.",
        ["https://blog.acme-running.example.com/vs-nimbus", "https://runnersworld-example.com/best-shoes-2026"],
    ),
    (
        "You can buy Acme running shoes directly from "
        "https://acme-running.example.com/shop or through major retailers. "
        "For support with an order, visit "
        "https://support.acme-running.example.com/returns.",
        ["https://acme-running.example.com/shop", "https://support.acme-running.example.com/returns"],
    ),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _get_or_none(session: AsyncSession, title_marker: str) -> Audit | None:
    result = await session.execute(
        select(Audit).where(
            Audit.project_id == PROJECT_ID,
            Audit.error_message.like(f"%{title_marker}%") if title_marker else Audit.error_message == "",
        )
    )
    return result.scalars().first()


async def _existing_by_marker(session: AsyncSession, marker: str) -> Audit | None:
    result = await session.execute(select(Audit).where(Audit.project_id == PROJECT_ID))
    for audit in result.scalars().all():
        if audit.configuration and audit.configuration.get("seed_marker") == marker:
            return audit
    return None


async def _load_context(session: AsyncSession):
    project = (
        await session.execute(select(Project).where(Project.id == PROJECT_ID))
    ).scalar_one()
    prompt_set = (
        await session.execute(
            select(PromptSet).where(PromptSet.project_id == PROJECT_ID)
        )
    ).scalars().first()
    prompts = (
        await session.execute(
            select(Prompt).where(Prompt.prompt_set_id == prompt_set.id).order_by(Prompt.created_at)
        )
    ).scalars().all()
    connections = {
        c.transport_provider: c
        for c in (
            await session.execute(
                select(ProviderConnection).where(
                    ProviderConnection.workspace_id == project.workspace_id
                )
            )
        ).scalars().all()
    }
    return project, list(prompts), connections


def _base_configuration(project: Project, marker: str) -> dict:
    return {
        "seed_marker": marker,
        "brand_name": project.brand_name,
        "benchmark_mode": project.benchmark_mode,
        "country_code": project.country_code,
        "language_code": project.language_code,
    }


async def _make_snapshots(session, audit, prompts, connections):
    prompt_snaps = []
    for idx, prompt in enumerate(prompts):
        snap = AuditPromptSnapshot(
            audit_id=audit.id,
            prompt_id=prompt.id,
            prompt_index=idx,
            text=prompt.text,
            theme=prompt.theme,
            intent=prompt.intent,
        )
        session.add(snap)
        prompt_snaps.append(snap)
    engine_snaps = {}
    for logical, transport, model in ENGINES:
        conn = connections.get(transport)
        snap = AuditEngineSnapshot(
            audit_id=audit.id,
            logical_engine=logical,
            transport_provider=transport,
            transport_model=model,
            connection_id=conn.id if conn else None,
        )
        session.add(snap)
        engine_snaps[logical] = snap
    await session.flush()
    return prompt_snaps, engine_snaps


async def seed_completed(session: AsyncSession, project, prompts, connections):
    marker = "seed-completed-v1"
    if await _existing_by_marker(session, marker):
        print("completed audit already seeded, skipping")
        return
    audit = Audit(
        workspace_id=project.workspace_id,
        project_id=project.id,
        status="completed",
        benchmark_mode=project.benchmark_mode,
        system_instruction="Answer naturally as a helpful shopping assistant.",
        repetitions=1,
        random_seed=str(random.getrandbits(64)),
        configuration=_base_configuration(project, marker),
        analyzer_version=ANALYZER_VERSION,
        requested_count=0,
        completed_count=0,
        failed_count=0,
        created_at=_utcnow() - timedelta(days=2),
        started_at=_utcnow() - timedelta(days=2),
        completed_at=_utcnow() - timedelta(days=2) + timedelta(minutes=8),
    )
    session.add(audit)
    await session.flush()

    prompt_snaps, engine_snaps = await _make_snapshots(session, audit, prompts, connections)
    audit.requested_count = len(prompt_snaps) * len(engine_snaps)

    analysis_ids = []
    artifact_ids = []
    completed = 0
    for p_idx, psnap in enumerate(prompt_snaps):
        for logical, transport, model in ENGINES:
            esnap = engine_snaps[logical]
            answer, citation_urls = SAMPLE_ANSWERS[p_idx % len(SAMPLE_ANSWERS)]
            task = AuditTask(
                audit_id=audit.id,
                workspace_id=project.workspace_id,
                prompt_snapshot_id=psnap.id,
                engine_snapshot_id=esnap.id,
                prompt_index=p_idx,
                repetition=0,
                randomized_position=p_idx,
                logical_engine=logical,
                transport_provider=transport,
                transport_model=model,
                prompt_text=psnap.text,
                idempotency_key=f"{audit.id}:{p_idx}:0:{logical}",
                status="succeeded",
                answer_text=answer,
                search_used=True,
                search_events=[{"sequence": 0, "query": psnap.text[:60], "call_id": "c0", "call_sequence": 0, "query_sequence": 0}],
                citations=[{"ordinal": i, "url": u, "domain": u.split("/")[2], "title": "", "start_index": 0, "end_index": 0, "cited_text": ""} for i, u in enumerate(citation_urls)],
                latency_ms=random.randint(400, 2200),
                completed_at=audit.created_at + timedelta(minutes=1 + p_idx),
            )
            session.add(task)
            await session.flush()

            artifact = RawResponseArtifact(
                audit_id=audit.id,
                task_id=task.id,
                logical_engine=logical,
                transport_provider=transport,
                transport_model=model,
                answer_text=answer,
                search_used=True,
                search_events=task.search_events,
                citations=task.citations,
                provider_metadata={"seed": True},
                usage={"input_tokens": 320, "output_tokens": 180},
                latency_ms=task.latency_ms,
            )
            session.add(artifact)
            await session.flush()
            task.result_artifact_id = artifact.id
            artifact_ids.append(str(artifact.id))

            session.add(ProviderAttempt(
                task_id=task.id, audit_id=audit.id, attempt_number=1,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                status="succeeded", latency_ms=task.latency_ms, artifact_id=artifact.id,
            ))

            brand_mentioned = "Acme" in answer
            owned_hit = any("acme-running.example.com" in u for u in citation_urls)
            unintended_hit = any("support.acme-running.example.com" in u for u in citation_urls)
            citations_rows = []
            for i, u in enumerate(citation_urls):
                domain = u.split("/")[2]
                if "acme-running.example.com" in domain and "support" not in domain:
                    classification, is_owned, is_unintended, matched = "owned", True, False, None
                elif "support.acme-running.example.com" in domain:
                    classification, is_owned, is_unintended, matched = "unintended", False, True, None
                elif "velocitysports" in domain or "nimbusathletics" in domain or "trailblazer" in domain:
                    classification, is_owned, is_unintended, matched = "competitor", False, False, "Velocity Sports"
                else:
                    classification, is_owned, is_unintended, matched = "third_party", False, False, None
                citations_rows.append(Citation(
                    workspace_id=project.workspace_id, audit_id=audit.id,
                    artifact_id=artifact.id, analyzer_version=ANALYZER_VERSION,
                    ordinal=i, url=u, title="", domain=domain,
                    classification=classification, is_owned=is_owned,
                    is_unintended=is_unintended, matched_competitor=matched,
                ))

            analysis = ResponseAnalysis(
                workspace_id=project.workspace_id, audit_id=audit.id, task_id=task.id,
                artifact_id=artifact.id, analyzer_version=ANALYZER_VERSION,
                scoring_rule_version=SCORING_RULE_VERSION,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                prompt_index=p_idx, repetition=0,
                prompt_class=psnap.intent or "unspecified",
                brand_mentioned=brand_mentioned,
                brand_first_offset=answer.find("Acme") if brand_mentioned else None,
                owned_domain_cited=owned_hit, owned_citation_count=sum(1 for c in citations_rows if c.is_owned),
                unintended_domain_cited=unintended_hit,
                citation_count=len(citations_rows), search_used=True, search_query_count=1,
                score={"brand_mentioned": brand_mentioned, "owned_domain_cited": owned_hit},
            )
            session.add(analysis)
            await session.flush()
            for c in citations_rows:
                c.analysis_id = analysis.id
                session.add(c)
            if brand_mentioned:
                session.add(BrandMention(
                    workspace_id=project.workspace_id, audit_id=audit.id, analysis_id=analysis.id,
                    artifact_id=artifact.id, analyzer_version=ANALYZER_VERSION,
                    brand_name=project.brand_name, first_offset=analysis.brand_first_offset,
                ))
            for c in citations_rows:
                if c.classification == "competitor":
                    session.add(CompetitorMention(
                        workspace_id=project.workspace_id, audit_id=audit.id, analysis_id=analysis.id,
                        artifact_id=artifact.id, analyzer_version=ANALYZER_VERSION,
                        competitor_name=c.matched_competitor or "Velocity Sports",
                    ))
            analysis_ids.append(str(analysis.id))
            completed += 1

    audit.completed_count = completed
    audit.failed_count = 0
    total_prompts = len(prompt_snaps)
    mention_rate = sum(1 for _ in range(completed)) and 0.83
    snapshot = MetricSnapshot(
        workspace_id=project.workspace_id, audit_id=audit.id, project_id=project.id,
        analyzer_version=ANALYZER_VERSION, scoring_rule_version=SCORING_RULE_VERSION,
        total_completed=completed, total_failed=0, visibility_score=83.0,
        metrics={
            "brand_mention_rate": 0.83, "owned_citation_rate": 0.61,
            # Real scoring shape: dict keyed by competitor name (scoring.py
            # `_competitor_aggregates`), not a bare float — the trends endpoint
            # iterates it (`set(metrics.get("competitor_mention_rate") or {})`).
            # NOTE: no `share_of_voice` block here, so the Visibility Trends
            # SOV chart / SOV ranking columns render empty in the harness.
            # Real audits populate it via scoring; enrich the seed if those
            # visuals need harness coverage.
            "competitor_mention_rate": {"Velocity Sports": 0.44}, "per_engine": {
                logical: {"mention_rate": 0.83} for logical, _, _ in ENGINES
            },
        },
        source_analysis_ids=analysis_ids, source_artifact_ids=artifact_ids,
    )
    session.add(snapshot)
    audit.summary = snapshot.metrics
    session.add(AuditEvent(audit_id=audit.id, event_type="audit.completed", message="status -> completed", payload={"status": "completed"}))
    await session.commit()
    print(f"Seeded completed audit {audit.id} ({completed} tasks)")


async def seed_partial(session: AsyncSession, project, prompts, connections):
    marker = "seed-partial-v1"
    if await _existing_by_marker(session, marker):
        print("partially_completed audit already seeded, skipping")
        return
    audit = Audit(
        workspace_id=project.workspace_id, project_id=project.id,
        status="partially_completed", benchmark_mode=project.benchmark_mode,
        system_instruction="Answer naturally as a helpful shopping assistant.",
        repetitions=1, random_seed=str(random.getrandbits(64)),
        configuration=_base_configuration(project, marker),
        analyzer_version=ANALYZER_VERSION,
        created_at=_utcnow() - timedelta(days=1),
        started_at=_utcnow() - timedelta(days=1),
        completed_at=_utcnow() - timedelta(days=1) + timedelta(minutes=5),
    )
    session.add(audit)
    await session.flush()
    prompt_snaps, engine_snaps = await _make_snapshots(session, audit, prompts[:3], connections)
    audit.requested_count = len(prompt_snaps) * len(engine_snaps)

    analysis_ids, artifact_ids = [], []
    completed = failed = 0
    for p_idx, psnap in enumerate(prompt_snaps):
        for e_idx, (logical, transport, model) in enumerate(ENGINES):
            esnap = engine_snaps[logical]
            should_fail = (p_idx + e_idx) % 3 == 0
            task = AuditTask(
                audit_id=audit.id, workspace_id=project.workspace_id,
                prompt_snapshot_id=psnap.id, engine_snapshot_id=esnap.id,
                prompt_index=p_idx, repetition=0, randomized_position=p_idx,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                prompt_text=psnap.text,
                idempotency_key=f"{audit.id}:{p_idx}:0:{logical}",
                status="failed" if should_fail else "succeeded",
                error_code="rate_limit" if should_fail else "",
                error_detail="Provider returned HTTP 429 (rate limited) after exhausting retries." if should_fail else "",
                attempt_count=5 if should_fail else 1,
                completed_at=audit.created_at + timedelta(minutes=1 + p_idx),
            )
            if not should_fail:
                answer, citation_urls = SAMPLE_ANSWERS[p_idx % len(SAMPLE_ANSWERS)]
                task.answer_text = answer
                task.search_used = True
                task.citations = [{"ordinal": i, "url": u, "domain": u.split("/")[2], "title": "", "start_index": 0, "end_index": 0, "cited_text": ""} for i, u in enumerate(citation_urls)]
            session.add(task)
            await session.flush()
            session.add(ProviderAttempt(
                task_id=task.id, audit_id=audit.id, attempt_number=task.attempt_count or 1,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                status="failed" if should_fail else "succeeded",
                error_code=task.error_code, error_detail=task.error_detail,
            ))
            if should_fail:
                failed += 1
                continue
            artifact = RawResponseArtifact(
                audit_id=audit.id, task_id=task.id, logical_engine=logical,
                transport_provider=transport, transport_model=model,
                answer_text=task.answer_text, search_used=True,
                citations=task.citations, usage={"input_tokens": 300, "output_tokens": 150},
            )
            session.add(artifact)
            await session.flush()
            task.result_artifact_id = artifact.id
            artifact_ids.append(str(artifact.id))
            analysis = ResponseAnalysis(
                workspace_id=project.workspace_id, audit_id=audit.id, task_id=task.id,
                artifact_id=artifact.id, analyzer_version=ANALYZER_VERSION,
                scoring_rule_version=SCORING_RULE_VERSION,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                prompt_index=p_idx, repetition=0, prompt_class=psnap.intent or "unspecified",
                brand_mentioned="Acme" in task.answer_text, citation_count=len(task.citations or []),
                search_used=True, search_query_count=1,
                score={"brand_mentioned": "Acme" in task.answer_text},
            )
            session.add(analysis)
            await session.flush()
            analysis_ids.append(str(analysis.id))
            completed += 1

    audit.completed_count = completed
    audit.failed_count = failed
    snapshot = MetricSnapshot(
        workspace_id=project.workspace_id, audit_id=audit.id, project_id=project.id,
        analyzer_version=ANALYZER_VERSION, scoring_rule_version=SCORING_RULE_VERSION,
        total_completed=completed, total_failed=failed,
        visibility_score=70.0 if completed else 0.0,
        metrics={"brand_mention_rate": 0.7, "note": "partial run -- some tasks rate-limited"},
        source_analysis_ids=analysis_ids, source_artifact_ids=artifact_ids,
    )
    session.add(snapshot)
    audit.summary = snapshot.metrics
    session.add(AuditEvent(audit_id=audit.id, event_type="audit.status", message="status -> partially_completed", payload={"status": "partially_completed"}))
    await session.commit()
    print(f"Seeded partially_completed audit {audit.id} ({completed} ok, {failed} failed)")


async def seed_failed(session: AsyncSession, project, prompts, connections):
    marker = "seed-failed-v1"
    if await _existing_by_marker(session, marker):
        print("failed audit already seeded, skipping")
        return
    audit = Audit(
        workspace_id=project.workspace_id, project_id=project.id,
        status="failed", benchmark_mode=project.benchmark_mode,
        system_instruction="Answer naturally as a helpful shopping assistant.",
        repetitions=1, random_seed=str(random.getrandbits(64)),
        configuration=_base_configuration(project, marker),
        analyzer_version="",
        error_message="All tasks failed: BYOK connection returned 401 Unauthorized (auth_failure).",
        created_at=_utcnow() - timedelta(hours=6),
        started_at=_utcnow() - timedelta(hours=6),
        completed_at=_utcnow() - timedelta(hours=6) + timedelta(minutes=2),
    )
    session.add(audit)
    await session.flush()
    prompt_snaps, engine_snaps = await _make_snapshots(session, audit, prompts[:2], connections)
    audit.requested_count = len(prompt_snaps) * len(engine_snaps)
    failed = 0
    for p_idx, psnap in enumerate(prompt_snaps):
        for logical, transport, model in ENGINES:
            esnap = engine_snaps[logical]
            task = AuditTask(
                audit_id=audit.id, workspace_id=project.workspace_id,
                prompt_snapshot_id=psnap.id, engine_snapshot_id=esnap.id,
                prompt_index=p_idx, repetition=0, randomized_position=p_idx,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                prompt_text=psnap.text,
                idempotency_key=f"{audit.id}:{p_idx}:0:{logical}",
                status="failed", error_code="auth_failure",
                error_detail="Provider returned HTTP 401 Unauthorized -- BYOK key invalid or revoked.",
                attempt_count=1,
                completed_at=audit.created_at + timedelta(minutes=1),
            )
            session.add(task)
            await session.flush()
            session.add(ProviderAttempt(
                task_id=task.id, audit_id=audit.id, attempt_number=1,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                status="failed", error_code="auth_failure",
                error_detail=task.error_detail,
            ))
            failed += 1
    audit.failed_count = failed
    audit.completed_count = 0
    session.add(AuditEvent(audit_id=audit.id, event_type="audit.status", message="status -> failed", payload={"status": "failed"}))
    await session.commit()
    print(f"Seeded failed audit {audit.id} ({failed} failed tasks)")


async def seed_running(session: AsyncSession, project, prompts, connections):
    marker = "seed-running-v1"
    if await _existing_by_marker(session, marker):
        print("running audit already seeded, skipping")
        return
    audit = Audit(
        workspace_id=project.workspace_id, project_id=project.id,
        status="running", benchmark_mode=project.benchmark_mode,
        system_instruction="Answer naturally as a helpful shopping assistant.",
        repetitions=1, random_seed=str(random.getrandbits(64)),
        configuration=_base_configuration(project, marker),
        analyzer_version="",
        created_at=_utcnow() - timedelta(minutes=3),
        started_at=_utcnow() - timedelta(minutes=3),
    )
    session.add(audit)
    await session.flush()
    prompt_snaps, engine_snaps = await _make_snapshots(session, audit, prompts, connections)
    audit.requested_count = len(prompt_snaps) * len(engine_snaps)
    for p_idx, psnap in enumerate(prompt_snaps):
        for e_idx, (logical, transport, model) in enumerate(ENGINES):
            esnap = engine_snaps[logical]
            is_first = p_idx == 0 and e_idx == 0
            # NOTE: available_at/lease_expires_at are pinned far in the future
            # (year ~2099) for these demo-only queued/running tasks. The real
            # audit_worker's claim() only takes QUEUED/RETRY_WAIT tasks whose
            # available_at <= now, and its sweeper reclaims LEASED/RUNNING
            # tasks whose lease_expires_at has passed back into RETRY_WAIT
            # (available immediately) once the lease TTL elapses. Without
            # this pin, the live worker (which has no real provider
            # credentials) would claim these "in-flight demo" tasks and burn
            # them into real failures within seconds/minutes of seeding,
            # silently corrupting the "running" demo audit into "failed".
            far_future = _utcnow() + timedelta(days=3650)
            task = AuditTask(
                audit_id=audit.id, workspace_id=project.workspace_id,
                prompt_snapshot_id=psnap.id, engine_snapshot_id=esnap.id,
                prompt_index=p_idx, repetition=0, randomized_position=p_idx,
                logical_engine=logical, transport_provider=transport, transport_model=model,
                prompt_text=psnap.text,
                idempotency_key=f"{audit.id}:{p_idx}:0:{logical}",
                status="running" if is_first else "queued",
                available_at=far_future,
                lease_owner="seed-demo-worker" if is_first else None,
                lease_expires_at=far_future if is_first else None,
                heartbeat_at=_utcnow() if is_first else None,
            )
            session.add(task)
    session.add(AuditEvent(audit_id=audit.id, event_type="audit.running", message="status -> running", payload={"status": "running"}))
    await session.commit()
    print(f"Seeded running audit {audit.id} (in-flight, no results yet)")


async def main():
    async with SessionLocal() as session:
        project, prompts, connections = await _load_context(session)
        if not prompts:
            print("No prompts found for project -- run the prompt-seeding step first.")
            return
        await seed_completed(session, project, prompts, connections)
        await seed_partial(session, project, prompts, connections)
        await seed_failed(session, project, prompts, connections)
        await seed_running(session, project, prompts, connections)


if __name__ == "__main__":
    asyncio.run(main())
