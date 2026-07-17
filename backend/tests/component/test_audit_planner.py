"""Planner: deterministic slot shuffle + cooperative cancel (invariants 9 + 3).

Exercises ``create_audit`` against a real Postgres schema:
  - a fixed seed reproduces the exact slot ordering (determinism);
  - one AuditTask is enqueued per (prompt x engine x repetition) slot with a
    stable idempotency key and frozen prompt/engine snapshots;
  - ``cancel_audit`` flips the audit to ``cancelled`` and terminalizes every
    non-terminal task so a live worker stops at its boundary.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.audits import (
    AUDIT_STATUS_CANCELLED,
    AUDIT_STATUS_QUEUED,
    TASK_STATUS_QUEUED,
)
from app.domain.audits.planner import (
    AuditValidationError,
    cancel_audit,
    create_audit,
    list_tasks,
)
from app.models.audit import AuditEngineSnapshot, AuditPromptSnapshot
from tests.component.audit_helpers import seed_audit_fixtures


async def _create(
    session: AsyncSession, seed, *, seed_value: str | None = None, reps: int = 2
):
    return await create_audit(
        session,
        workspace_id=seed.workspace_id,
        project_id=seed.project_id,
        engines=seed.engines,
        prompt_set_id=seed.prompt_set_id,
        repetitions=reps,
        random_seed=seed_value,
    )


@pytest.mark.asyncio
async def test_create_audit_enqueues_one_task_per_slot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=3)
    async with session_factory() as session:
        audit = await _create(session, seed, seed_value="12345", reps=2)

        assert audit.status == AUDIT_STATUS_QUEUED
        # 3 prompts x 1 engine x 2 reps = 6 tasks.
        assert audit.requested_count == 6

        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert len(tasks) == 6
        assert {t.status for t in tasks} == {TASK_STATUS_QUEUED}
        # Idempotency keys are unique + stable-shaped.
        keys = {t.idempotency_key for t in tasks}
        assert len(keys) == 6
        for task in tasks:
            assert task.idempotency_key == (
                f"{audit.id}:{task.prompt_index}:{task.repetition}:"
                f"{task.logical_engine}"
            )

        # Snapshots frozen.
        prompts = (
            await session.scalars(
                select(AuditPromptSnapshot).where(
                    AuditPromptSnapshot.audit_id == audit.id
                )
            )
        ).all()
        assert len(prompts) == 3
        engines = (
            await session.scalars(
                select(AuditEngineSnapshot).where(
                    AuditEngineSnapshot.audit_id == audit.id
                )
            )
        ).all()
        assert len(engines) == 1


@pytest.mark.asyncio
async def test_fixed_seed_reproduces_slot_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed_a = await seed_audit_fixtures(
            session, prompt_count=4, email="a@example.com"
        )
    async with session_factory() as session:
        seed_b = await seed_audit_fixtures(
            session, prompt_count=4, email="b@example.com"
        )

    async with session_factory() as session:
        audit_a = await _create(session, seed_a, seed_value="99", reps=3)
        order_a = [
            (t.prompt_index, t.repetition, t.logical_engine)
            for t in sorted(
                await list_tasks(
                    session,
                    workspace_id=seed_a.workspace_id,
                    audit_id=audit_a.id,
                ),
                key=lambda t: t.randomized_position,
            )
        ]
    async with session_factory() as session:
        audit_b = await _create(session, seed_b, seed_value="99", reps=3)
        order_b = [
            (t.prompt_index, t.repetition, t.logical_engine)
            for t in sorted(
                await list_tasks(
                    session,
                    workspace_id=seed_b.workspace_id,
                    audit_id=audit_b.id,
                ),
                key=lambda t: t.randomized_position,
            )
        ]

    # Same seed -> identical shuffle order (determinism, invariant 9).
    assert order_a == order_b
    # Stored seed is preserved for replay.
    assert audit_a.random_seed == "99"


@pytest.mark.asyncio
async def test_cancel_audit_terminalizes_open_tasks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=2)
    async with session_factory() as session:
        audit = await _create(session, seed, seed_value="7", reps=2)

        cancelled = await cancel_audit(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert cancelled.status == AUDIT_STATUS_CANCELLED

        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"cancelled"}
        assert all(t.lease_owner is None for t in tasks)

        # Cancelling a terminal audit is rejected.
        with pytest.raises(AuditValidationError):
            await cancel_audit(
                session, workspace_id=seed.workspace_id, audit_id=audit.id
            )


@pytest.mark.asyncio
async def test_create_audit_rejects_engine_without_route(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(
            session, prompt_count=1, engines=["gemini"]
        )
    async with session_factory() as session:
        with pytest.raises(AuditValidationError):
            await create_audit(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                engines=["claude"],  # no route configured
                prompt_set_id=seed.prompt_set_id,
                repetitions=1,
            )


@pytest.mark.asyncio
async def test_create_audit_ignores_inactive_legacy_route(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A retired (inactive) legacy route must not resolve for a new audit.

    Even though the connection is active, an ``active=false`` OpenRouter route
    is excluded by the planner, so the engine has no usable route.
    """
    from app.models.provider import ProviderConnection, ProviderRoute

    async with session_factory() as session:
        seed = await seed_audit_fixtures(
            session, prompt_count=1, engines=["gemini"]
        )

    async with session_factory() as session:
        connection = ProviderConnection(
            workspace_id=seed.workspace_id,
            label="Legacy OpenRouter",
            transport_provider="openrouter",
            api_key_encrypted="x",
            active=True,
        )
        session.add(connection)
        await session.flush()
        session.add(
            ProviderRoute(
                workspace_id=seed.workspace_id,
                connection_id=connection.id,
                logical_engine="chatgpt",
                transport_provider="openrouter",
                transport_model="openai/gpt-5.4",
                is_default=True,
                active=False,
                deactivation_reason="openrouter_retired_v2",
            )
        )
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(AuditValidationError):
            await create_audit(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                engines=["chatgpt"],  # only a retired route exists
                prompt_set_id=seed.prompt_set_id,
                repetitions=1,
            )


@pytest.mark.asyncio
async def test_create_audit_rejects_unknown_or_disabled_prompt_ids(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    import uuid

    from sqlalchemy import select

    from app.models.prompt import Prompt

    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=3)

    # Disable one of the seeded prompts.
    async with session_factory() as session:
        disabled_id = seed.prompt_ids[0]
        prompt = await session.get(Prompt, disabled_id)
        prompt.enabled = False
        await session.commit()

    # A request that includes the disabled prompt is rejected, not silently
    # narrowed to the enabled subset.
    async with session_factory() as session:
        with pytest.raises(AuditValidationError):
            await create_audit(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                engines=seed.engines,
                prompt_ids=seed.prompt_ids,  # includes the disabled one
                repetitions=1,
            )

    # A request that references a completely unknown id is also rejected.
    async with session_factory() as session:
        with pytest.raises(AuditValidationError):
            await create_audit(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                engines=seed.engines,
                prompt_ids=[seed.prompt_ids[1], uuid.uuid4()],
                repetitions=1,
            )

    # Sanity: an explicit list of only enabled, in-project ids still works.
    async with session_factory() as session:
        enabled = (
            await session.scalars(
                select(Prompt.id)
                .join(Prompt.prompt_set)
                .where(Prompt.enabled.is_(True))
            )
        ).all()
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_ids=[seed.prompt_ids[1], seed.prompt_ids[2]],
            repetitions=1,
        )
        assert audit.requested_count == 2
        assert len(enabled) == 2
