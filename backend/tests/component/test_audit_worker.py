"""AuditWorker: claim -> call (mocked) -> persist -> finalize (invariants 3, 8, 9).

Provider calls are MOCKED (no network, no spend). Exercises the real
claim/lease loop against a Postgres schema:
  - a full audit runs every task to ``succeeded``, writes one immutable
    RawResponseArtifact + ProviderAttempt each, scores each on persist, and
    finalizes RUNNING -> ANALYZING -> REPORTING -> COMPLETED with an aggregated
    MetricSnapshot (B6);
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
from app.connectors.answer_engines.errors import ProviderError
from app.core.config.audits import (
    ATTEMPT_STATUS_FAILED,
    ATTEMPT_STATUS_SUCCEEDED,
    AUDIT_STATUS_CANCELLED,
    AUDIT_STATUS_COMPLETED,
    audit_settings,
)
from app.core.config.provider_catalog import (
    ENGINE_CHATGPT,
    ENGINE_GEMINI,
    ERROR_INVALID_SURFACE,
    ERROR_RATE_LIMIT,
    TRANSPORT_GOOGLE,
    TRANSPORT_OPENAI,
)
from app.domain.audits.planner import cancel_audit, create_audit, list_tasks
from app.models.analysis import MetricSnapshot, ResponseAnalysis
from app.models.audit import (
    Audit,
    AuditTask,
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

        # Each succeeded task was scored on persist (B6, invariant 4).
        assert all(t.score is not None for t in tasks)

        refreshed = await session.get(Audit, audit.id)
        # Execution complete -> analysis stage runs -> audit COMPLETED (B6).
        assert refreshed.status == AUDIT_STATUS_COMPLETED
        assert refreshed.completed_count == 6
        assert refreshed.failed_count == 0
        assert refreshed.started_at is not None
        assert refreshed.completed_at is not None

        # One aggregated MetricSnapshot with a populated Visibility Score.
        snapshot = await session.scalar(
            select(MetricSnapshot).where(MetricSnapshot.audit_id == audit.id)
        )
        assert snapshot is not None
        assert snapshot.total_completed == 6
        assert snapshot.total_failed == 0
        # The stub always mentions "Acme" (the brand) -> 100% Visibility.
        assert snapshot.visibility_score == 100.0
        assert snapshot.analyzer_version

        # One ResponseAnalysis per succeeded execution (invariant 4).
        analyses = await session.scalar(
            select(func.count())
            .select_from(ResponseAnalysis)
            .where(ResponseAnalysis.audit_id == audit.id)
        )
        assert analyses == 6


class _OpenAIStubAdapter(_StubAdapter):
    """OpenAI direct stub: records the chatgpt/openai provenance triple."""

    logical_engine = ENGINE_CHATGPT
    transport_provider = TRANSPORT_OPENAI


@pytest.mark.asyncio
async def test_worker_persists_openai_provenance(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A ChatGPT audit executes over the direct ``openai`` transport and freezes
    # the chatgpt/openai/gpt-5.4 provenance triple on the task + attempt.
    async with session_factory() as session:
        seed = await seed_audit_fixtures(
            session, prompt_count=1, engines=[ENGINE_CHATGPT]
        )
    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=1,
            random_seed="1",
        )

    monkeypatch.setattr(audit_worker, "build_adapter", lambda **_: _OpenAIStubAdapter())
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)

    worker = AuditWorker(session_factory=session_factory, owner="w-openai")
    await worker.run_until_idle()

    async with session_factory() as session:
        task = await session.scalar(
            select(AuditTask).where(AuditTask.audit_id == audit.id)
        )
        assert task.status == "succeeded"
        assert task.logical_engine == ENGINE_CHATGPT
        assert task.transport_provider == TRANSPORT_OPENAI
        assert task.transport_model == "gpt-5.4"
        assert task.result_artifact_id is not None


@pytest.mark.asyncio
async def test_worker_rejects_frozen_openrouter_task_without_network(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A task frozen before the v2 retirement still points at the ``openrouter``
    # transport. The worker must fail it terminally with ``invalid_surface``
    # BEFORE the connection-activity check, key decryption, or any network call
    # (invariant 6/10) — build_adapter must never be reached.
    seed, audit = await _make_audit(session_factory, prompts=1, reps=1)

    # Rewrite the frozen task + engine snapshot to the retired transport, as a
    # persisted pre-v2 OpenRouter task would look.
    async with session_factory() as session:
        from app.models.audit import AuditEngineSnapshot

        task = await session.scalar(
            select(AuditTask).where(AuditTask.audit_id == audit.id)
        )
        task.transport_provider = "openrouter"
        snapshot = await session.get(AuditEngineSnapshot, task.engine_snapshot_id)
        if snapshot is not None:
            snapshot.transport_provider = "openrouter"
        await session.commit()

    def _boom(**_: object):  # noqa: ANN202
        raise AssertionError("build_adapter must not be called for a retired transport")

    monkeypatch.setattr(audit_worker, "build_adapter", _boom)
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)

    worker = AuditWorker(session_factory=session_factory, owner="w-frozen")
    await worker.run_until_idle()

    async with session_factory() as session:
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"failed"}
        assert {t.error_code for t in tasks} == {ERROR_INVALID_SURFACE}
        # No external provider call was made (build_adapter would have raised)
        # → no raw artifact is persisted (invariant 6/10). The single terminal
        # bookkeeping attempt documents the rejection, not a network round-trip.
        artifacts = await session.scalar(
            select(func.count())
            .select_from(RawResponseArtifact)
            .where(RawResponseArtifact.audit_id == audit.id)
        )
        assert artifacts == 0
        attempts = (
            await session.scalars(
                select(ProviderAttempt).where(ProviderAttempt.audit_id == audit.id)
            )
        ).all()
        assert all(a.status == "failed" for a in attempts)
        assert all(a.error_code == ERROR_INVALID_SURFACE for a in attempts)
        assert all(a.artifact_id is None for a in attempts)


@pytest.mark.asyncio
async def test_worker_stops_at_boundary_when_cancelled(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _make_audit(session_factory, prompts=2, reps=1)  # 2

    # Kill the audit before the worker picks anything up.
    async with session_factory() as session:
        await cancel_audit(session, workspace_id=seed.workspace_id, audit_id=audit.id)

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


class _HookAdapter(_StubAdapter):
    """Runs an async callback mid-call, then returns a normal success.

    Simulates something happening on the row (cancel, lease loss) WHILE the
    provider call is in flight, so the persist-time owner/liveness guard can be
    exercised.
    """

    def __init__(self, hook) -> None:
        self._hook = hook

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        await self._hook()
        return await super().execute(request)


@pytest.mark.asyncio
async def test_worker_discards_success_when_cancelled_mid_call(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A user cancels the audit while the provider call is in flight. The
    # in-flight worker must NOT persist success evidence for a cancelled task.
    seed, audit = await _make_audit(session_factory, prompts=1, reps=1)

    async def _cancel_mid_call() -> None:
        async with session_factory() as session:
            await cancel_audit(
                session, workspace_id=seed.workspace_id, audit_id=audit.id
            )

    monkeypatch.setattr(
        audit_worker, "build_adapter", lambda **_: _HookAdapter(_cancel_mid_call)
    )
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)

    worker = AuditWorker(session_factory=session_factory, owner="w-midcancel")
    await worker.run_until_idle()

    async with session_factory() as session:
        refreshed = await session.get(Audit, audit.id)
        assert refreshed.status == AUDIT_STATUS_CANCELLED
        # The stale success was discarded: no artifact/attempt/analysis rows.
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
        analyses = await session.scalar(
            select(func.count())
            .select_from(ResponseAnalysis)
            .where(ResponseAnalysis.audit_id == audit.id)
        )
        assert artifacts == 0
        assert attempts == 0
        assert analyses == 0
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"cancelled"}
        assert all(t.result_artifact_id is None for t in tasks)


@pytest.mark.asyncio
async def test_worker_discards_success_when_lease_lost_mid_call(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Worker A's lease expires mid-call and Worker B claims the task. When A
    # returns it must NOT write rows for a task it no longer owns (invariant 3/8).
    seed, audit = await _make_audit(session_factory, prompts=1, reps=1)

    async def _steal_lease() -> None:
        async with session_factory() as session:
            task = await session.scalar(
                select(AuditTask).where(AuditTask.audit_id == audit.id)
            )
            task.lease_owner = "worker-b"  # another worker holds it now
            await session.commit()

    monkeypatch.setattr(
        audit_worker, "build_adapter", lambda **_: _HookAdapter(_steal_lease)
    )
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)

    worker = AuditWorker(session_factory=session_factory, owner="worker-a")
    await worker.run_until_idle()

    async with session_factory() as session:
        # Stale Worker A wrote nothing.
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
        assert artifacts == 0
        assert attempts == 0
        task = await session.scalar(
            select(AuditTask).where(AuditTask.audit_id == audit.id)
        )
        # The task still belongs to Worker B, not finalized by the stale worker.
        assert task.lease_owner == "worker-b"
        assert task.status == "running"
        assert task.result_artifact_id is None


class _FlakyAdapter(_StubAdapter):
    """Fails with a retryable error ``fail_times`` times, then succeeds."""

    def __init__(self, *, fail_times: int) -> None:
        self._fail_times = fail_times
        self.calls = 0

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ProviderError(
                "temporary rate limit",
                error_code=ERROR_RATE_LIMIT,
                retryable=True,
            )
        return await super().execute(request)


@pytest.mark.asyncio
async def test_worker_records_one_attempt_per_provider_call(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two retryable failures then a success -> three append-only ProviderAttempt
    # rows (invariant 3: one row per attempt), not a single collapsed row.
    seed, audit = await _make_audit(session_factory, prompts=1, reps=1)

    adapter = _FlakyAdapter(fail_times=2)
    monkeypatch.setattr(audit_worker, "build_adapter", lambda **_: adapter)
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)
    # Zero the delay knobs so the internal retry loop is fast + deterministic.
    monkeypatch.setattr(audit_settings, "retry_base_delay_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "retry_jitter_seconds", 0.0)

    worker = AuditWorker(session_factory=session_factory, owner="w-flaky")
    await worker.run_until_idle()

    async with session_factory() as session:
        task = await session.scalar(
            select(AuditTask).where(AuditTask.audit_id == audit.id)
        )
        assert task.status == "succeeded"
        assert task.attempt_count == 3

        attempts = (
            await session.scalars(
                select(ProviderAttempt)
                .where(ProviderAttempt.audit_id == audit.id)
                .order_by(ProviderAttempt.attempt_number.asc())
            )
        ).all()
        assert len(attempts) == 3
        assert [a.status for a in attempts] == [
            ATTEMPT_STATUS_FAILED,
            ATTEMPT_STATUS_FAILED,
            ATTEMPT_STATUS_SUCCEEDED,
        ]
        assert [a.attempt_number for a in attempts] == [1, 2, 3]
        # The first two carry the retryable error; the last carries the artifact.
        assert attempts[0].error_code == ERROR_RATE_LIMIT
        assert attempts[1].error_code == ERROR_RATE_LIMIT
        assert attempts[-1].artifact_id is not None

        # Exactly one immutable artifact for the single successful call.
        artifacts = await session.scalar(
            select(func.count())
            .select_from(RawResponseArtifact)
            .where(RawResponseArtifact.audit_id == audit.id)
        )
        assert artifacts == 1
