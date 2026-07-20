# Content-generation domain service (workspace-scoped, invariant 5).
#
# Owns enqueue (race-safe workspace-scoped idempotency + provider-config
# check + frozen website-context/message snapshots), the bounded history
# list, detail, cancel, regenerate (context rebuilt) and try-again (frozen
# snapshot reused). Every query filters by the caller's workspace; a record
# in another workspace is indistinguishable from a missing one (404).
#
# The provider API key never appears here — the worker resolves it at call
# time from config (invariant 6).
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.content import (
    CONTENT_GENERATOR_VERSION,
    CONTENT_KNOWN_PROVIDERS,
    CONTENT_LIST_MAX_LIMIT,
    CONTEXT_STATUS_DISABLED,
    content_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_TERMINAL_STATUSES,
)
from app.domain.content.message_builder import build_messages
from app.domain.content.schemas import (
    ContentGenerationDetail,
    ContentGenerationListItem,
    WebsiteContextSummary,
    prompt_preview,
)
from app.domain.content.website_context import (
    WebsiteContext,
    build_website_context,
)
from app.models.content import ContentGeneration
from app.models.project import Project


class ContentGenerationNotFoundError(LookupError):
    """Record/project missing or owned by another workspace (-> 404)."""


class ProviderNotConfiguredError(RuntimeError):
    """The content provider key is not configured (-> 409)."""


class IdempotencyConflictError(RuntimeError):
    """Same idempotency key, different request fingerprint (-> 409)."""


class CancelNotAllowedError(RuntimeError):
    """Cancel requested on a terminal record (-> 409)."""


def request_fingerprint(
    *,
    project_id: uuid.UUID,
    prompt: str,
    output_type: str,
    website_context_enabled: bool,
) -> str:
    """Stable comparator for idempotency replay-vs-conflict decisions."""
    canonical = "\x1f".join(
        [
            str(project_id),
            prompt.strip(),
            output_type,
            "1" if website_context_enabled else "0",
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _project_in_workspace(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
    )
    if project is None:
        raise ContentGenerationNotFoundError("Project not found")
    return project


def _summary_dto(row: ContentGeneration) -> WebsiteContextSummary | None:
    snapshot = row.website_context_snapshot or {}
    summary = snapshot.get("summary")
    if not summary:
        return None
    return WebsiteContextSummary(
        crawl_id=str(summary.get("crawl_id", "")),
        crawl_completed_at=summary.get("crawl_completed_at"),
        extractor_version=summary.get("extractor_version", ""),
        analyzer_version=summary.get("analyzer_version", ""),
        page_count=int(summary.get("page_count", 0)),
        char_count=int(summary.get("char_count", 0)),
        site_url_ids=list(summary.get("site_url_ids", [])),
        artifact_ids=list(summary.get("artifact_ids", [])),
        content_hashes=list(summary.get("content_hashes", [])),
    )


def to_list_item(row: ContentGeneration) -> ContentGenerationListItem:
    item = ContentGenerationListItem.model_validate(row)
    item.prompt_preview = prompt_preview(row.prompt)
    return item


def to_detail(row: ContentGeneration) -> ContentGenerationDetail:
    detail = ContentGenerationDetail.model_validate(row)
    detail.prompt_preview = prompt_preview(row.prompt)
    detail.website_context_summary = _summary_dto(row)
    return detail


async def _insert_generation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    prompt: str,
    output_type: str,
    website_context_enabled: bool,
    website_context: WebsiteContext,
    idempotency_key: str,
    fingerprint: str,
) -> ContentGeneration:
    messages, digest, message_snapshot = build_messages(
        prompt=prompt,
        output_type=output_type,
        website_context=website_context,
    )
    # ``messages`` itself is never persisted — the worker rebuilds it from the
    # frozen prompt + snapshot; only the digest + safe snapshot are stored.
    del messages
    row = ContentGeneration(
        workspace_id=workspace_id,
        project_id=project_id,
        prompt=prompt,
        output_type=output_type,
        website_context_enabled=website_context_enabled,
        website_context_status=website_context.status,
        website_context_snapshot=website_context.snapshot(),
        request_fingerprint=fingerprint,
        message_digest=digest,
        message_snapshot=message_snapshot,
        idempotency_key=idempotency_key,
        provider=content_settings.provider,
        requested_model=content_settings.model,
        generator_version=CONTENT_GENERATOR_VERSION,
    )
    session.add(row)
    return row


def _require_provider_configured() -> None:
    # Readiness is provider-aware: an unknown provider name is just as
    # unconfigured as a missing key, and each known provider is checked for
    # the key it actually uses (only Mistral exists today).
    if content_settings.provider not in CONTENT_KNOWN_PROVIDERS:
        raise ProviderNotConfiguredError(
            f"unknown content provider: {content_settings.provider}"
        )
    if not content_settings.mistral_api_key.get_secret_value():
        raise ProviderNotConfiguredError(
            "content provider is not configured (missing API key)"
        )


async def enqueue_generation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    prompt: str,
    output_type: str,
    website_context_enabled: bool,
    idempotency_key: str = "",
) -> tuple[ContentGeneration, bool]:
    """Enqueue one generation. Returns ``(row, created)``.

    ``created`` is False on an idempotent replay (same workspace key + same
    fingerprint). A same-key different-fingerprint request raises
    ``IdempotencyConflictError``; a concurrent same-key insert converges via
    the IntegrityError reload/compare path.
    """
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )

    fingerprint = request_fingerprint(
        project_id=project_id,
        prompt=prompt,
        output_type=output_type,
        website_context_enabled=website_context_enabled,
    )
    # A server-side key when the client sent none: the composite constraint is
    # always satisfied and keyless requests never collide with each other.
    key = idempotency_key or str(uuid.uuid4())

    # Replay before the provider-config check: a retry of an already-accepted
    # request must stay retrievable even if the provider was unconfigured (or
    # broken) in between.
    if idempotency_key:
        existing = await session.scalar(
            select(ContentGeneration).where(
                ContentGeneration.workspace_id == workspace_id,
                ContentGeneration.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint == fingerprint:
                return existing, False
            raise IdempotencyConflictError(
                "idempotency key was already used with a different request"
            )

    _require_provider_configured()

    if website_context_enabled:
        website_context = await build_website_context(
            session, workspace_id=workspace_id, project_id=project_id
        )
    else:
        website_context = WebsiteContext(status=CONTEXT_STATUS_DISABLED)

    row = await _insert_generation(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        prompt=prompt,
        output_type=output_type,
        website_context_enabled=website_context_enabled,
        website_context=website_context,
        idempotency_key=key,
        fingerprint=fingerprint,
    )
    try:
        await session.commit()
    except IntegrityError:
        # Concurrent same-key insert: converge on the committed winner.
        await session.rollback()
        winner = await session.scalar(
            select(ContentGeneration).where(
                ContentGeneration.workspace_id == workspace_id,
                ContentGeneration.idempotency_key == key,
            )
        )
        if winner is None:
            raise
        if winner.request_fingerprint == fingerprint:
            return winner, False
        raise IdempotencyConflictError(
            "idempotency key was already used with a different request"
        ) from None
    await session.refresh(row)
    return row, True


async def list_generations(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    limit: int,
) -> list[ContentGeneration]:
    """The project's generations, newest first, bounded (authorizes first)."""
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    capped = max(1, min(limit, CONTENT_LIST_MAX_LIMIT))
    rows = await session.scalars(
        select(ContentGeneration)
        .where(
            ContentGeneration.workspace_id == workspace_id,
            ContentGeneration.project_id == project_id,
        )
        .order_by(
            ContentGeneration.created_at.desc(),
            ContentGeneration.id.desc(),
        )
        .limit(capped)
    )
    return list(rows.all())


async def get_generation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    generation_id: uuid.UUID,
) -> ContentGeneration:
    row = await session.scalar(
        select(ContentGeneration).where(
            ContentGeneration.id == generation_id,
            ContentGeneration.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise ContentGenerationNotFoundError("Content generation not found")
    return row


async def cancel_generation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    generation_id: uuid.UUID,
) -> ContentGeneration:
    """Cooperative cancel: allowed for any non-terminal status.

    Sets ``cancelled`` + clears the lease under ``FOR UPDATE`` so a worker's
    later terminal write (which re-checks owner + status under its own lock)
    discards the in-flight result (invariant 3/9).
    """
    await get_generation(
        session, workspace_id=workspace_id, generation_id=generation_id
    )
    locked = await session.get(ContentGeneration, generation_id, with_for_update=True)
    if locked is None:
        raise ContentGenerationNotFoundError("Content generation not found")
    if locked.status in TASK_TERMINAL_STATUSES:
        # Capture before rollback: rollback expires the instance and a later
        # attribute access would trigger sync lazy-loading (MissingGreenlet).
        terminal_status = locked.status
        await session.rollback()
        raise CancelNotAllowedError(f"cannot cancel a {terminal_status} generation")
    locked.status = TASK_STATUS_CANCELLED
    locked.lease_owner = None
    locked.lease_expires_at = None
    locked.completed_at = datetime.now(UTC)
    if not locked.error_code:
        locked.error_code = "cancelled"
    await session.commit()
    await session.refresh(locked)
    return locked


async def regenerate(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    generation_id: uuid.UUID,
) -> ContentGeneration:
    """New record from an existing one, context REBUILT from the newest
    eligible crawl. The original is never mutated."""
    source = await get_generation(
        session, workspace_id=workspace_id, generation_id=generation_id
    )
    row, _created = await enqueue_generation(
        session,
        workspace_id=workspace_id,
        project_id=source.project_id,
        prompt=source.prompt,
        output_type=source.output_type,
        website_context_enabled=source.website_context_enabled,
    )
    return row


async def try_again(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    generation_id: uuid.UUID,
) -> ContentGeneration:
    """New record re-using the source's exact frozen context snapshot
    (reproducible; no rebuild). The original is never mutated."""
    source = await get_generation(
        session, workspace_id=workspace_id, generation_id=generation_id
    )
    _require_provider_configured()
    snapshot = source.website_context_snapshot or {}
    frozen = WebsiteContext(
        status=source.website_context_status,
        pages=list(snapshot.get("pages") or []),
        summary=snapshot.get("summary"),
    )
    fingerprint = request_fingerprint(
        project_id=source.project_id,
        prompt=source.prompt,
        output_type=source.output_type,
        website_context_enabled=source.website_context_enabled,
    )
    row = await _insert_generation(
        session,
        workspace_id=workspace_id,
        project_id=source.project_id,
        prompt=source.prompt,
        output_type=source.output_type,
        website_context_enabled=source.website_context_enabled,
        website_context=frozen,
        idempotency_key=str(uuid.uuid4()),
        fingerprint=fingerprint,
    )
    await session.commit()
    await session.refresh(row)
    return row
