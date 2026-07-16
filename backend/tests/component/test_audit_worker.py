"""AuditWorker: claim -> call (mocked) -> persist -> finalize (invariants 3, 8, 9).

Provider calls are MOCKED (no network, no spend). Exercises the real
claim/lease loop against a Postgres schema:
  - a full audit runs every task to ``succeeded``, writes one immutable
    RawResponseArtifact + ProviderAttempt each, and finalizes RUNNING ->
    ANALYZING;
  - a cooperatively-cancelled audit stops at the task boundary (no artifact);
  - the per-run wall-clock deadline terminalizes remaining tasks.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.core.config.audits import (
    AUDIT_STATUS_ANALYZING,
    AUDIT_STATUS_CANCELLED,
    audit_settings,
)
from app.core.config.provider_catalog import ENGINE_GEMINI, TRANSPORT_GOOGLE
from app.domain.audits.planner import cancel_audit, create_audit, list_tasks
from app.models.audit import (
    Audit,
    ProviderAttempt,
    RawResponseArtifact,
)
from app.workers import audit_worker
from app.workers.audit_worker import AuditWorker
from tests.component.audit_helpers import seed_audit_fixtures


class _StubAdapter:
    """In-memory stand-in for an answer-engine adapter (no network)."""

    logical_engine = ENGINE_GEMINI
    transport_provider = TRANSPORT_GOOGLE

    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        return AnswerEngineResponse(
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            transport_model=request.model,
            answer_text=f"Acme is a great option for {request.prompt}.",
            search_used=True,
            search_events=(SearchEventResult(sequence=0, query=request.prompt),),
            citations=(
                CitationResult(
                    ordinal=0,
                    url="https://acme.com/",
                    title="Acme",
                    domain="acme.com",
                    start_index=0,
                    end_index=4,
                    cited_text="Acme",
                ),
            ),
            provider_metadata={"query_text_available": True},
            usage={"input_tokens": 10, "output_tokens": 20},
            latency_ms=5,
        )


@pytest.fixture
def _stub_adapter(monkeypatch: pytest.MonkeyPatch):
    def _build(**_: object) -> _StubAdapter:
        return _StubAdapter()

    monkeypatch.setattr(audit_worker, "build_adapter", _build)
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)


async def _make_audit(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    prompts: int,
    reps: int,
):
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=prompts)
    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=reps,
            random_seed="1",
        )
        return seed, audit


@pytest.mark.asyncio
async def test_worker_runs_all_tasks_and_finalizes(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _make_audit(session_factory, prompts=3, reps=2)  # 6
    worker = AuditWorker(session_factory=session_factory, owner="w-test")

    await worker.run_until_idle()

    async with session_factory() as session:
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"succeeded"}
        assert all(t.answer_text for t in tasks)
        assert all(t.result_artifact_id is not None for t in tasks)

        # One immutable artifact + one attempt per task (invariant 3).
        artifacts = await session.scalar(
            select(func.count())
            .select_from(RawResponseArtifact)
            .where(RawResponseArtifact.audit_id == audit.id)
        )
        attempts = await session.scalar(
            select(func.count())
            .select_from(ProviderAttempt)
            .where(ProviderAttempt.audit_id == audit.id)
        )
        assert artifacts == 6
        assert attempts == 6

        refreshed = await session.get(Audit, audit.id)
        # Execution complete -> audit ready for analysis (B6).
        assert refreshed.status == AUDIT_STATUS_ANALYZING
        assert refreshed.completed_count == 6
        assert refreshed.failed_count == 0
        assert refreshed.started_at is not None


@pytest.mark.asyncio
async def test_worker_stops_at_boundary_when_cancelled(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _make_audit(session_factory, prompts=2, reps=1)  # 2

    # Kill the audit before the worker picks anything up.
    async with session_factory() as session:
        await cancel_audit(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )

    worker = AuditWorker(session_factory=session_factory, owner="w-cancel")
    await worker.run_until_idle()

    async with session_factory() as session:
        refreshed = await session.get(Audit, audit.id)
        assert refreshed.status == AUDIT_STATUS_CANCELLED
        # No provider was called -> no artifacts.
        artifacts = await session.scalar(
            select(func.count())
            .select_from(RawResponseArtifact)
            .where(RawResponseArtifact.audit_id == audit.id)
        )
        assert artifacts == 0
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"cancelled"}


@pytest.mark.asyncio
async def test_worker_cuts_off_at_run_deadline(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deadline already elapsed the instant a task starts -> every task hits the
    # cutoff at its boundary before calling the (stub) provider.
    monkeypatch.setattr(audit_settings, "max_run_seconds", 0.0)
    seed, audit = await _make_audit(session_factory, prompts=2, reps=1)  # 2

    # Mark the audit started so the deadline math trips immediately.
    async with session_factory() as session:
        from datetime import UTC, datetime

        refreshed = await session.get(Audit, audit.id)
        refreshed.started_at = datetime.now(UTC)
        await session.commit()

    worker = AuditWorker(session_factory=session_factory, owner="w-deadline")
    await worker.run_until_idle()

    async with session_factory() as session:
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"failed"}
        assert {t.error_code for t in tasks} == {"run_deadline_exceeded"}
        artifacts = await session.scalar(
            select(func.count())
            .select_from(RawResponseArtifact)
            .where(RawResponseArtifact.audit_id == audit.id)
        )
        assert artifacts == 0


@pytest.mark.asyncio
async def test_worker_fails_task_with_missing_connection(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _make_audit(session_factory, prompts=1, reps=1)  # 1

    # Deactivate the connection so key resolution fails terminally.
    async with session_factory() as session:
        from app.models.provider import ProviderConnection

        conns = (
            await session.scalars(
                select(ProviderConnection).where(
                    ProviderConnection.workspace_id == seed.workspace_id
                )
            )
        ).all()
        for conn in conns:
            conn.active = False
        await session.commit()

    worker = AuditWorker(session_factory=session_factory, owner="w-noconn")
    await worker.run_until_idle()

    async with session_factory() as session:
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"failed"}
        assert {t.error_code for t in tasks} == {"provider_connection_missing"}
