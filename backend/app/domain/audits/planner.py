# Audit planner (invariant 9 — deterministic; invariant 3 — frozen snapshots).
#
# Adapts the reference ``ai_visibility/service.create_run`` + ``cancel_run`` to
# Searchify's workspace-scoped, UUID, BYOK-routed model. ``create_audit``:
#   1. resolves + authorizes the project and prompt source (workspace-scoped);
#   2. resolves one provider route per requested logical engine from the
#      workspace's ``ProviderConnection``s (never the key — invariant 6);
#   3. freezes prompt + engine + scoring snapshots (invariant 3);
#   4. generates one slot per (prompt x engine x repetition), shuffles them with
#      the stored 64-bit seed (invariant 9), and enqueues one ``AuditTask`` per
#      slot with a stable idempotency key.
# ``cancel_audit`` is cooperative: it flips the audit to ``cancelled`` and
# terminalizes unfinished tasks so a live worker stops at its boundary.
from __future__ import annotations

import hashlib
import random
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config.audits import (
    AUDIT_ACTIVE_STATUSES,
    AUDIT_STATUS_CANCELLED,
    AUDIT_STATUS_DRAFT,
    AUDIT_STATUS_QUEUED,
    AUDIT_STATUS_VALIDATING,
    EVENT_AUDIT_CANCELLED,
    EVENT_AUDIT_CREATED,
    EVENT_AUDIT_QUEUED,
    TASK_STATUS_CANCELLED,
    TASK_TERMINAL_STATUSES,
    audit_settings,
    system_instruction_for_mode,
)
from app.core.config.projects import (
    BENCHMARK_MODES,
    DEFAULT_BENCHMARK_MODE,
    MAX_REPETITIONS,
    MIN_REPETITIONS,
)
from app.core.config.prompts import PROMPT_STATUS_ACTIVE
from app.core.config.provider_catalog import (
    LOGICAL_ENGINES,
    is_route_approved,
)
from app.domain.audits.state_events import apply_transition, record_event
from app.domain.products.shim import project_product_identity
from app.domain.projects.shim import project_scoring_identity
from app.models.audit import (
    Audit,
    AuditEngineSnapshot,
    AuditPromptSnapshot,
    AuditTask,
)
from app.models.brand import Brand
from app.models.product import CompetitorProduct
from app.models.project import Project
from app.models.prompt import Prompt, PromptSet
from app.models.provider import ProviderConnection, ProviderRoute


class AuditValidationError(ValueError):
    """Raised when an audit request is invalid (bad prompts/engines/routes)."""


class AuditNotFoundError(LookupError):
    """Raised when an audit is missing or not in the caller's workspace."""


def _normalize_seed(value: str | None) -> str:
    """Return a decimal string for a 64-bit unsigned seed.

    Accepts an explicit seed (any 64-bit-representable int, decimal string) or
    generates a fresh 64-bit one when omitted (invariant 9 — stored + replayed).
    """
    if value is None or not str(value).strip():
        return str(secrets.randbits(64))
    try:
        seed_int = int(str(value).strip())
    except ValueError as exc:
        raise AuditValidationError("random_seed must be an integer") from exc
    # Keep it in the unsigned 64-bit range so replay is exact.
    return str(seed_int & ((1 << 64) - 1))


def _prompt_panel_snapshot(rows: list[dict]) -> dict:
    """Stable hash of the frozen prompt panel (audit-scoping evidence)."""
    import json

    encoded = json.dumps(rows, sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "panel_id": digest[:16],
        "panel_hash": digest,
        "prompt_hashes": [
            hashlib.sha256(str(r["text"]).encode("utf-8")).hexdigest() for r in rows
        ],
    }


async def _load_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    result = await session.execute(
        select(Project)
        .options(
            selectinload(Project.brand).selectinload(Brand.aliases),
            selectinload(Project.competitors),
            selectinload(Project.owned_domains),
            selectinload(Project.unintended_domains),
            selectinload(Project.products),
            selectinload(Project.competitor_products).selectinload(
                CompetitorProduct.competitor
            ),
        )
        .where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
    )
    project = result.scalars().unique().one_or_none()
    if project is None:
        raise AuditValidationError("Project not found")
    return project


async def _resolve_prompts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    prompt_set_id: uuid.UUID | None,
    prompt_ids: list[uuid.UUID],
) -> list[Prompt]:
    """Resolve active, enabled prompts from a set or explicit ids, workspace-scoped."""
    stmt = (
        select(Prompt)
        .join(PromptSet, PromptSet.id == Prompt.prompt_set_id)
        .join(Project, Project.id == PromptSet.project_id)
        .where(
            Project.workspace_id == workspace_id,
            Project.id == project_id,
            Prompt.enabled.is_(True),
            # Proposed (unreviewed AI suggestions) and archived prompts are
            # never audit-eligible — only human-accepted active prompts run.
            Prompt.status == PROMPT_STATUS_ACTIVE,
        )
        .order_by(Prompt.created_at.asc())
    )
    if prompt_ids:
        stmt = stmt.where(Prompt.id.in_(prompt_ids))
    elif prompt_set_id is not None:
        stmt = stmt.where(Prompt.prompt_set_id == prompt_set_id)
    else:
        raise AuditValidationError("Either prompt_set_id or prompt_ids is required")
    prompts = list((await session.scalars(stmt)).all())
    # For an explicit id list, reject the whole request if any requested prompt
    # is missing / disabled / from another project or workspace, rather than
    # silently auditing a smaller set than the caller asked for.
    if prompt_ids:
        requested = set(prompt_ids)
        resolved_ids = {prompt.id for prompt in prompts}
        unavailable = requested - resolved_ids
        if unavailable:
            missing = ", ".join(str(pid) for pid in sorted(map(str, unavailable)))
            raise AuditValidationError(
                f"Prompt(s) not found, disabled, not active, or not in this "
                f"project: {missing}"
            )
    if not prompts:
        raise AuditValidationError("No enabled prompts to audit")
    return prompts


async def _resolve_routes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    engines: list[str],
) -> dict[str, tuple[ProviderRoute, ProviderConnection]]:
    """Pick one active route + connection per requested logical engine.

    Prefers a route flagged ``is_default`` for the engine, else the first
    active one. Raises if an engine is unknown or has no configured route.
    """
    normalized = [str(e).strip().lower() for e in engines]
    seen: set[str] = set()
    unique_engines: list[str] = []
    for engine in normalized:
        if engine not in LOGICAL_ENGINES:
            raise AuditValidationError(f"Unknown logical engine: {engine}")
        if engine not in seen:
            seen.add(engine)
            unique_engines.append(engine)

    result = await session.execute(
        select(ProviderRoute, ProviderConnection)
        .join(
            ProviderConnection,
            ProviderConnection.id == ProviderRoute.connection_id,
        )
        .where(
            ProviderRoute.workspace_id == workspace_id,
            ProviderRoute.active.is_(True),
            ProviderConnection.active.is_(True),
        )
        .order_by(
            ProviderRoute.is_default.desc(),
            ProviderRoute.created_at.asc(),
        )
    )
    routes: dict[str, tuple[ProviderRoute, ProviderConnection]] = {}
    for route, connection in result.all():
        if not is_route_approved(route.logical_engine, route.transport_provider):
            continue
        routes.setdefault(route.logical_engine, (route, connection))

    resolved: dict[str, tuple[ProviderRoute, ProviderConnection]] = {}
    missing: list[str] = []
    for engine in unique_engines:
        if engine in routes:
            resolved[engine] = routes[engine]
        else:
            missing.append(engine)
    if missing:
        raise AuditValidationError(
            "No active provider route configured for engine(s): " + ", ".join(missing)
        )
    return resolved


def _resolve_benchmark_mode(value: str | None, project: Project) -> str:
    mode = str(value or project.benchmark_mode or DEFAULT_BENCHMARK_MODE)
    mode = mode.strip().lower()
    if mode not in BENCHMARK_MODES:
        raise AuditValidationError(f"Unsupported benchmark_mode: {mode}")
    return mode


async def create_audit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    engines: list[str],
    prompt_set_id: uuid.UUID | None = None,
    prompt_ids: list[uuid.UUID] | None = None,
    repetitions: int | None = None,
    benchmark_mode: str | None = None,
    random_seed: str | None = None,
) -> Audit:
    """Create + enqueue an audit (freeze snapshots, deterministic slot shuffle).

    Commits with all tasks ``queued`` so the worker can claim them.
    """
    project = await _load_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    prompts = await _resolve_prompts(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        prompt_set_id=prompt_set_id,
        prompt_ids=list(prompt_ids or []),
    )
    routes = await _resolve_routes(session, workspace_id=workspace_id, engines=engines)

    reps = int(repetitions or project.default_repetitions or 1)
    if reps < MIN_REPETITIONS or reps > MAX_REPETITIONS:
        raise AuditValidationError(
            f"repetitions must be between {MIN_REPETITIONS} and {MAX_REPETITIONS}"
        )

    engine_list = list(routes.keys())
    total = len(prompts) * len(engine_list) * reps
    if total > audit_settings.max_tasks_per_audit:
        raise AuditValidationError(
            f"Audit would create {total} tasks, exceeding the limit of "
            f"{audit_settings.max_tasks_per_audit}"
        )

    mode = _resolve_benchmark_mode(benchmark_mode, project)
    seed = _normalize_seed(random_seed)
    system_instruction = system_instruction_for_mode(
        mode=mode,
        country_code=project.country_code,
        language_code=project.language_code,
    )

    prompt_rows = [
        {
            "text": prompt.text or "",
            "theme": prompt.theme or "",
            "intent": prompt.intent or "",
        }
        for prompt in prompts
    ]
    scoring_identity = project_scoring_identity(project)
    configuration = {
        **scoring_identity,
        # Frozen product catalog (Agentic Commerce): the deterministic
        # product analyzer scores against this copy, so later catalog edits
        # never alter the audit (invariant 9).
        **project_product_identity(project),
        "benchmark_mode": mode,
        "engines": engine_list,
        "repetitions": reps,
        "max_attempts": audit_settings.max_attempts,
        "max_call_seconds": audit_settings.max_call_seconds,
        "max_run_seconds": audit_settings.max_run_seconds,
        "request_timeout_seconds": audit_settings.request_timeout_seconds,
        "engine_routes": {
            engine: {
                "logical_engine": engine,
                "transport_provider": route.transport_provider,
                "transport_model": route.transport_model,
                "connection_id": str(connection.id),
            }
            for engine, (route, connection) in routes.items()
        },
        **_prompt_panel_snapshot(prompt_rows),
    }

    audit = Audit(
        workspace_id=workspace_id,
        project_id=project.id,
        status=AUDIT_STATUS_DRAFT,
        benchmark_mode=mode,
        system_instruction=system_instruction,
        repetitions=reps,
        random_seed=seed,
        configuration=configuration,
        requested_count=total,
    )
    session.add(audit)
    await session.flush()  # assign audit.id

    # Freeze prompt snapshots (immutable copies, invariant 3).
    prompt_snapshots: list[AuditPromptSnapshot] = []
    for index, prompt in enumerate(prompts):
        snapshot = AuditPromptSnapshot(
            audit_id=audit.id,
            prompt_id=prompt.id,
            prompt_index=index,
            text=prompt.text or "",
            theme=prompt.theme or "",
            intent=prompt.intent or "",
        )
        session.add(snapshot)
        prompt_snapshots.append(snapshot)

    # Freeze engine snapshots (provenance triple + connection, invariant 10).
    engine_snapshots: dict[str, AuditEngineSnapshot] = {}
    for engine, (route, connection) in routes.items():
        engine_snapshot = AuditEngineSnapshot(
            audit_id=audit.id,
            logical_engine=engine,
            transport_provider=route.transport_provider,
            transport_model=route.transport_model,
            connection_id=connection.id,
            base_url=connection.base_url or "",
        )
        session.add(engine_snapshot)
        engine_snapshots[engine] = engine_snapshot
    await session.flush()  # assign snapshot ids

    # Build every (prompt_index, engine, repetition) slot, then shuffle it
    # deterministically with the stored seed (invariant 9). The same seed
    # reproduces the same order.
    slots = [
        (prompt_index, engine, repetition)
        for prompt_index in range(len(prompts))
        for engine in engine_list
        for repetition in range(reps)
    ]
    random.Random(int(seed)).shuffle(slots)

    for position, (prompt_index, engine, repetition) in enumerate(slots):
        prompt_snapshot = prompt_snapshots[prompt_index]
        engine_snapshot = engine_snapshots[engine]
        route, connection = routes[engine]
        idempotency_key = f"{audit.id}:{prompt_index}:{repetition}:{engine}"
        session.add(
            AuditTask(
                audit_id=audit.id,
                workspace_id=workspace_id,
                prompt_snapshot_id=prompt_snapshot.id,
                engine_snapshot_id=engine_snapshot.id,
                prompt_index=prompt_index,
                repetition=repetition,
                randomized_position=position,
                logical_engine=engine,
                transport_provider=route.transport_provider,
                transport_model=route.transport_model,
                prompt_text=prompt_snapshot.text,
                provider_route_snapshot={
                    "logical_engine": engine,
                    "transport_provider": route.transport_provider,
                    "transport_model": route.transport_model,
                    "connection_id": str(connection.id),
                    "base_url": connection.base_url or "",
                },
                idempotency_key=idempotency_key,
                max_attempts=audit_settings.max_attempts,
            )
        )

    # Move DRAFT -> VALIDATING -> QUEUED through the state machine so an illegal
    # move raises instead of silently corrupting the lifecycle (invariant 9).
    apply_transition(
        session,
        audit=audit,
        target=AUDIT_STATUS_VALIDATING,
        message="audit validating",
    )
    apply_transition(
        session,
        audit=audit,
        target=AUDIT_STATUS_QUEUED,
        message="audit queued",
    )
    record_event(
        session,
        audit_id=audit.id,
        event_type=EVENT_AUDIT_CREATED,
        message="audit created",
        payload={"requested_count": total, "engines": engine_list},
    )
    record_event(
        session,
        audit_id=audit.id,
        event_type=EVENT_AUDIT_QUEUED,
        message="audit queued",
        payload={"task_count": len(slots)},
    )

    await session.commit()
    # `engine_snapshots` is a lazy relationship; a bare ``session.refresh``
    # only reloads scalar columns, so accessing it later (e.g. from
    # ``AuditResponse.model_validate`` in the API layer, outside of an async
    # greenlet) raises ``MissingGreenlet``. Re-fetch through ``get_audit``,
    # which eagerly loads it via ``selectinload``, so the returned instance is
    # safe to serialize.
    return await get_audit(session, workspace_id=workspace_id, audit_id=audit.id)


async def get_audit(
    session: AsyncSession, *, workspace_id: uuid.UUID, audit_id: uuid.UUID
) -> Audit:
    result = await session.execute(
        select(Audit)
        .options(selectinload(Audit.engine_snapshots))
        .where(
            Audit.id == audit_id,
            Audit.workspace_id == workspace_id,
        )
    )
    audit = result.scalars().unique().one_or_none()
    if audit is None:
        raise AuditNotFoundError(str(audit_id))
    return audit


async def list_audits(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[Audit]:
    stmt = (
        select(Audit)
        .options(selectinload(Audit.engine_snapshots))
        .where(Audit.workspace_id == workspace_id)
        .order_by(Audit.created_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        stmt = stmt.where(Audit.project_id == project_id)
    return list((await session.scalars(stmt)).unique().all())


async def list_tasks(
    session: AsyncSession, *, workspace_id: uuid.UUID, audit_id: uuid.UUID
) -> list[AuditTask]:
    await get_audit(session, workspace_id=workspace_id, audit_id=audit_id)
    stmt = (
        select(AuditTask)
        .where(AuditTask.audit_id == audit_id)
        .order_by(AuditTask.randomized_position.asc())
    )
    return list((await session.scalars(stmt)).all())


async def cancel_audit(
    session: AsyncSession, *, workspace_id: uuid.UUID, audit_id: uuid.UUID
) -> Audit:
    """Cooperatively cancel an active audit and terminalize open tasks.

    Flips the audit to ``cancelled`` (so a live worker stops at the next
    execution boundary) and marks any non-terminal task ``cancelled`` so counts
    and the UI stay consistent. This also cleans up a zombie audit whose worker
    died mid-run.
    """
    audit = await get_audit(session, workspace_id=workspace_id, audit_id=audit_id)
    if audit.status not in AUDIT_ACTIVE_STATUSES:
        raise AuditValidationError("Only active audits can be cancelled")
    now = datetime.now(UTC)
    audit.completed_at = now
    # Route the flip through the state machine (invariant 9): AUDIT_ACTIVE_STATUSES
    # only contains statuses the machine allows to reach CANCELLED, so this never
    # raises here, but it keeps the single enforcement path and records the event.
    apply_transition(
        session,
        audit=audit,
        target=AUDIT_STATUS_CANCELLED,
        message="audit cancelled",
    )
    await session.execute(
        update(AuditTask)
        .where(AuditTask.audit_id == audit.id)
        .where(AuditTask.status.not_in(list(TASK_TERMINAL_STATUSES)))
        .values(
            status=TASK_STATUS_CANCELLED,
            lease_owner=None,
            lease_expires_at=None,
            completed_at=now,
            error_code="cancelled",
        )
    )
    record_event(
        session,
        audit_id=audit.id,
        event_type=EVENT_AUDIT_CANCELLED,
        message="audit cancelled",
        payload={"status": AUDIT_STATUS_CANCELLED},
    )
    await session.commit()
    # See the comment in ``create_audit``: refresh() would expire (and later
    # lazy-load) ``engine_snapshots``, which needs to stay eagerly loaded for
    # safe serialization outside the async greenlet.
    return await get_audit(session, workspace_id=workspace_id, audit_id=audit.id)
