# Integration sync worker: claims IntegrationSyncRun queue rows and pages the
# provider data APIs (GSC / GA4 / Bing) behind the config-owned dispatch
# registry (``INTEGRATION_CLIENT_BUILDERS`` — the ``_build_client`` seam).
#
# A separate process (the ``integration-worker`` compose service). It mirrors
# ``ContentWorker`` exactly on the queue mechanics — claim via the generic
# ``PostgresTaskQueue`` (``FOR UPDATE SKIP LOCKED``, claim committed BEFORE
# any network I/O — invariant 8), sweep expired leases FIRST in every loop
# iteration, ``mark_running`` before provider I/O, and heartbeat the lease
# while a long backfill pages. Cooperative cancel at the page boundary
# (invariant 9): the worker stops BEFORE the next provider call.
#
# Per claimed run (spec docs/roadmap/integrations.md §4):
#   1. begin the attempt (bump ``attempt_count``, append sync_started event);
#   2. resolve + decrypt the grant token, refreshing it when near expiry via
#      the SERIALIZED-PER-GRANT rotation (spec §2 — see ``_fresh_access_token``);
#   3. page every configured provider dataset over the run's window, writing
#      ONE immutable ``IntegrationImportArtifact`` per fetched page
#      (invariant 3: sha256 ``payload_hash``, credential-free
#      ``query_snapshot``, never an overwrite — a retry RESUMES from the
#      durable artifacts instead of refetching);
#   4. derive ``IntegrationMetricRow`` rows from the artifacts (projection,
#      never a second fetch — invariant 7) and call the C5
#      ``enqueue_post_sync_projections`` hook as the final step;
#   5. on success stamp ``connection.last_synced_at`` + the sync_finished
#      event and succeed the run.
#
# Every write transaction re-locks the run row FOR UPDATE and re-checks
# ``lease_owner``/status: a lost lease or a cancelled run writes NOTHING
# (single-writer, invariant 3).
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.integrations import oauth as integration_oauth
from app.connectors.integrations.bing import BingApiError
from app.connectors.integrations.ga4 import Ga4ApiError
from app.connectors.integrations.gsc import GscApiError
from app.core.config.integrations import (
    ERROR_GRANT_AUTH_FAILED,
    ERROR_PAYLOAD_TOO_LARGE,
    ERROR_PROVIDER_API,
    ERROR_TOKEN_REFRESH_FAILED,
    ERROR_UNMAPPED_PROPERTY,
    EVENT_INTEGRATION_REAUTH_REQUIRED,
    EVENT_INTEGRATION_SYNC_FINISHED,
    EVENT_INTEGRATION_SYNC_STARTED,
    GRANT_STATUS_CONNECTED,
    GRANT_STATUS_NEEDS_REAUTH,
    INTEGRATION_CLIENT_BUILDERS,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_QUEUE_SPEC,
    IntegrationDatasetTemplate,
    integration_settings,
)
from app.core.config.provider_catalog import ERROR_UNKNOWN
from app.core.config.task_queue import (
    TASK_STATUS_FAILED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    TASK_TERMINAL_STATUSES,
)
from app.core.database import SessionLocal
from app.core.security import decrypt_secret, encrypt_secret
from app.core.telemetry import configure_logging
from app.domain.analytics.enqueue import enqueue_post_sync_projections
from app.domain.integrations.derive import UnmappedPropertyError, derive_run
from app.models.integrations import (
    IntegrationConnection,
    IntegrationEvent,
    IntegrationImportArtifact,
    IntegrationOAuthGrant,
    IntegrationSyncRun,
)
from app.orchestration.postgres_task_queue import PostgresTaskQueue

logger = logging.getLogger("app.workers.integration_worker")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _UnsupportedProviderError(RuntimeError):
    """The run's provider has no data-API client in the config registry."""


class _PayloadTooLargeError(RuntimeError):
    """A fetched page exceeds the inline-payload cap (rejected, not truncated)."""


class _ClientPage(Protocol):
    """One fetched provider page (every provider client's return shape)."""

    payload: dict
    rows: tuple[dict, ...]


class _DataClient(Protocol):
    """The uniform paging contract the worker dispatches through.

    Mirrors the GSC reference client: every provider client exposes this
    one method + signature and returns a page carrying ``payload``/``rows``.
    """

    async def query_search_analytics(
        self,
        *,
        access_token: str,
        property_ref: str,
        dimensions: Sequence[str],
        start_date: date,
        end_date: date,
        start_row: int,
    ) -> _ClientPage: ...


# The classified provider-error taxonomy every client raises (GSC-shaped:
# config-owned error token + retryable + Retry-After advice).
_PROVIDER_API_ERRORS = (GscApiError, Ga4ApiError, BingApiError)


@dataclass(frozen=True)
class _RunContext:
    """The immutable identity of one claimed run, captured at attempt start."""

    run_id: uuid.UUID
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    grant_id: uuid.UUID
    provider: str
    transport: str
    grant_status: str
    sync_kind: str
    window_start: date
    window_end: date
    resync_seq: int
    property_ref: str
    attempt_count: int
    max_attempts: int


def _provider_datasets(provider: str) -> list[IntegrationDatasetTemplate]:
    """The config-owned dataset templates for one provider (C1 order)."""
    return [
        template
        for template in INTEGRATION_DATASET_TEMPLATES.values()
        if template.provider == provider
    ]


class IntegrationWorker:
    """Claim/lease loop for ``IntegrationSyncRun`` rows.

    ``transport`` is the test seam: an ``httpx.MockTransport`` (or any
    ``httpx.AsyncBaseTransport``) makes the real OAuth + GSC clients run
    without a network. Production passes none.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owner: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._session_factory = session_factory or SessionLocal
        self._queue = PostgresTaskQueue(self._session_factory, INTEGRATION_QUEUE_SPEC)
        self._transport = transport
        self.owner = owner or f"integration-worker-{uuid.uuid4().hex[:12]}"

    # --- Loop -------------------------------------------------------------

    async def run_once(self) -> int:
        """Sweep expired leases, claim one row, run it. Returns count run."""
        await self._queue.release_expired()
        rows = await self._queue.claim(owner=self.owner, limit=1)
        for row in rows:
            await self._execute(row)
        return len(rows)

    async def run_until_idle(self, *, max_batches: int = 1000) -> int:
        """Drain the queue until a claim returns nothing (test/one-shot)."""
        total = 0
        for _ in range(max_batches):
            ran = await self.run_once()
            if ran == 0:
                break
            total += ran
        return total

    async def run_forever(self) -> None:  # pragma: no cover - process loop
        logger.info("integration worker started", extra={"owner": self.owner})
        while True:
            try:
                ran = await self.run_once()
            except Exception:  # defensive: a bad row must not kill the loop
                logger.exception("integration worker loop iteration failed")
                ran = 0
            if ran == 0:
                await asyncio.sleep(
                    max(0.05, integration_settings.poll_interval_seconds)
                )

    # --- One claimed row --------------------------------------------------

    async def _execute(self, claimed: IntegrationSyncRun) -> None:
        run_id = claimed.id
        try:
            # Cooperative cancel at the boundary: if the run was cancelled
            # between enqueue and claim, never touch the provider.
            async with self._session_factory() as session:
                row = await session.get(IntegrationSyncRun, run_id)
                if row is None or row.status in TASK_TERMINAL_STATUSES:
                    return

            if not await self._queue.mark_running(task_id=run_id, owner=self.owner):
                # Lease lost before the work started; another worker retries.
                return

            ctx = await self._begin_attempt(run_id)
            if ctx is None:
                return
            await self._run(ctx)
        except Exception as exc:  # defensive: never kill the loop
            logger.exception(
                "integration sync crashed", extra={"sync_run_id": str(run_id)}
            )
            with contextlib.suppress(Exception):
                await self._queue.fail(
                    task_id=run_id,
                    owner=self.owner,
                    error_code=ERROR_UNKNOWN,
                    error_detail=f"worker crash: {type(exc).__name__}",
                )

    async def _begin_attempt(self, run_id: uuid.UUID) -> _RunContext | None:
        """Bump the attempt count + append sync_started (owner-gated)."""
        async with self._session_factory() as session:
            run = await session.get(IntegrationSyncRun, run_id, with_for_update=True)
            if (
                run is None
                or run.lease_owner != self.owner
                or run.status != TASK_STATUS_RUNNING
            ):
                await session.commit()  # nothing staged; releases the lock
                return None
            connection = await session.get(IntegrationConnection, run.connection_id)
            grant = (
                await session.get(IntegrationOAuthGrant, connection.grant_id)
                if connection is not None
                else None
            )
            if connection is None or grant is None:
                await session.commit()
                return None
            run.attempt_count += 1
            ctx = _RunContext(
                run_id=run.id,
                workspace_id=run.workspace_id,
                connection_id=connection.id,
                grant_id=grant.id,
                provider=connection.provider,
                transport=grant.transport,
                grant_status=grant.status,
                sync_kind=run.sync_kind,
                window_start=run.window_start,
                window_end=run.window_end,
                resync_seq=run.resync_seq,
                property_ref=connection.account_ref,
                attempt_count=run.attempt_count,
                max_attempts=run.max_attempts,
            )
            session.add(
                IntegrationEvent(
                    workspace_id=ctx.workspace_id,
                    connection_id=ctx.connection_id,
                    grant_id=ctx.grant_id,
                    event_type=EVENT_INTEGRATION_SYNC_STARTED,
                    message=f"Sync started for {ctx.provider}",
                    payload={
                        "provider": ctx.provider,
                        "sync_run_id": str(ctx.run_id),
                        "sync_kind": ctx.sync_kind,
                        "window_start": ctx.window_start.isoformat(),
                        "window_end": ctx.window_end.isoformat(),
                        "resync_seq": ctx.resync_seq,
                        "attempt_count": ctx.attempt_count,
                    },
                )
            )
            await session.commit()
            return ctx

    async def _run(self, ctx: _RunContext) -> None:
        if ctx.grant_status != GRANT_STATUS_CONNECTED:
            await self._queue.fail(
                task_id=ctx.run_id,
                owner=self.owner,
                error_code=ERROR_GRANT_AUTH_FAILED,
                error_detail=f"grant status is {ctx.grant_status!r}",
            )
            return
        try:
            client = self._build_client(ctx.provider)
        except _UnsupportedProviderError as exc:
            await self._queue.fail(
                task_id=ctx.run_id,
                owner=self.owner,
                error_code=ERROR_PROVIDER_API,
                error_detail=str(exc),
            )
            return

        try:
            access_token = await self._fresh_access_token(ctx)
        except integration_oauth.IntegrationOAuthError as exc:
            await self._handle_refresh_failure(ctx, exc)
            return

        heartbeat = asyncio.create_task(self._heartbeat_loop(ctx.run_id))
        try:
            for template in _provider_datasets(ctx.provider):
                synced = await self._sync_dataset(
                    ctx,
                    client=client,
                    template=template,
                    access_token=access_token,
                )
                if not synced:
                    # Lost lease or cancelled at a page boundary: not ours to
                    # finalize — nothing more is written.
                    return
        except _PROVIDER_API_ERRORS as exc:
            # Every provider client raises the same classified taxonomy.
            await self._handle_provider_error(ctx, exc)
            return
        except _PayloadTooLargeError as exc:
            await self._queue.fail(
                task_id=ctx.run_id,
                owner=self.owner,
                error_code=ERROR_PAYLOAD_TOO_LARGE,
                error_detail=str(exc),
            )
            return
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        await self._finalize_success(ctx)

    def _build_client(self, provider: str) -> _DataClient:
        """Provider -> data-API client dispatch via the config-owned registry.

        ``INTEGRATION_CLIENT_BUILDERS`` (config, invariant 1) maps each
        provider to its lazy client builder; an unmapped provider fails the
        run terminally (no retry burn).
        """
        builder = INTEGRATION_CLIENT_BUILDERS.get(provider)
        if builder is None:
            raise _UnsupportedProviderError(
                f"no data-API client for provider {provider!r}"
            )
        return builder(transport=self._transport)

    # --- Token refresh (serialized per grant, spec section 2) --------------

    async def _fresh_access_token(self, ctx: _RunContext) -> str:
        """Resolve a usable access token, refreshing when near expiry.

        The grant row is locked ``SELECT ... FOR UPDATE`` and the expiry is
        re-read INSIDE the lock: the exchange + re-encrypt runs only when the
        token is still near-expiry, so two workers sharing one grant perform
        exactly ONE remote refresh (the loser re-reads a fresh expiry and
        skips its call). The row lock is held across the refresh exchange —
        the spec-§2 serialization point and the documented carve-out from
        commit-before-I/O (the QUEUE claim itself was committed long before);
        it is released before any other provider I/O.
        """
        client = integration_oauth.build_oauth_client(
            ctx.transport, transport=self._transport
        )
        async with self._session_factory() as session:
            grant = await session.get(
                IntegrationOAuthGrant, ctx.grant_id, with_for_update=True
            )
            if grant is None:
                await session.commit()
                raise integration_oauth.IntegrationOAuthError(
                    "grant row is missing", error_code=ERROR_GRANT_AUTH_FAILED
                )
            now = _utcnow()
            skew = timedelta(seconds=integration_settings.token_refresh_skew_seconds)
            near_expiry = (
                grant.token_expires_at is None
                or grant.token_expires_at <= now + skew
            )
            if not near_expiry:
                access_token = decrypt_secret(grant.access_token_encrypted)
                # Release the grant row lock BEFORE provider I/O.
                await session.commit()
                return access_token
            if not grant.refresh_token_encrypted:
                await session.commit()
                raise integration_oauth.IntegrationOAuthError(
                    "grant has no refresh token", error_code=ERROR_GRANT_AUTH_FAILED
                )
            refresh_token = decrypt_secret(grant.refresh_token_encrypted)
            try:
                bundle = await client.refresh(refresh_token=refresh_token)
            except BaseException:
                # Never hold the grant row lock across a failed exchange.
                await session.rollback()
                raise
            grant.access_token_encrypted = encrypt_secret(bundle.access_token)
            if bundle.refresh_token:
                grant.refresh_token_encrypted = encrypt_secret(bundle.refresh_token)
            grant.token_expires_at = (
                now + timedelta(seconds=bundle.expires_in)
                if bundle.expires_in is not None
                else None
            )
            if bundle.granted_scopes:
                grant.granted_scopes = list(bundle.granted_scopes)
            await session.commit()
            return bundle.access_token

    async def _handle_refresh_failure(
        self, ctx: _RunContext, exc: integration_oauth.IntegrationOAuthError
    ) -> None:
        if exc.error_code == ERROR_GRANT_AUTH_FAILED:
            await self._mark_grant_needs_reauth(ctx)
            await self._queue.fail(
                task_id=ctx.run_id,
                owner=self.owner,
                error_code=exc.error_code,
                error_detail=str(exc),
            )
            return
        if exc.retryable:
            if ctx.attempt_count < ctx.max_attempts:
                await self._queue.retry(
                    task_id=ctx.run_id,
                    owner=self.owner,
                    delay_seconds=integration_settings.retry_delay(ctx.attempt_count),
                    error_code=ERROR_TOKEN_REFRESH_FAILED,
                    error_detail=str(exc),
                )
            else:
                await self._queue.fail(
                    task_id=ctx.run_id,
                    owner=self.owner,
                    error_code=INTEGRATION_QUEUE_SPEC.max_attempts_error,
                    error_detail=str(exc),
                )
            return
        await self._queue.fail(
            task_id=ctx.run_id,
            owner=self.owner,
            error_code=ERROR_TOKEN_REFRESH_FAILED,
            error_detail=str(exc),
        )

    async def _mark_grant_needs_reauth(self, ctx: _RunContext) -> None:
        """Transition a live grant to needs_reauth + append the event."""
        async with self._session_factory() as session:
            grant = await session.get(
                IntegrationOAuthGrant, ctx.grant_id, with_for_update=True
            )
            if grant is None or grant.status != GRANT_STATUS_CONNECTED:
                await session.commit()
                return
            grant.status = GRANT_STATUS_NEEDS_REAUTH
            session.add(
                IntegrationEvent(
                    workspace_id=ctx.workspace_id,
                    connection_id=ctx.connection_id,
                    grant_id=ctx.grant_id,
                    event_type=EVENT_INTEGRATION_REAUTH_REQUIRED,
                    message=(
                        "Grant token rejected by provider; re-authentication required"
                    ),
                    payload={
                        "provider": ctx.provider,
                        "transport": ctx.transport,
                        "sync_run_id": str(ctx.run_id),
                        "error_code": ERROR_GRANT_AUTH_FAILED,
                    },
                )
            )
            await session.commit()

    # --- Paging + immutable artifacts ---------------------------------------

    async def _sync_dataset(
        self,
        ctx: _RunContext,
        *,
        client: _DataClient,
        template: IntegrationDatasetTemplate,
        access_token: str,
    ) -> bool:
        """Page one dataset to completion. False = lost lease / cancelled."""
        page_size = integration_settings.sync_page_size
        start_row, complete = await self._dataset_resume(ctx.run_id, template.dataset)
        if complete:
            return True
        while True:
            # Cooperative cancel / lost-lease at the PAGE BOUNDARY: stop
            # BEFORE the next provider call (invariant 9).
            if not await self._still_owned(ctx.run_id):
                return False
            page = await client.query_search_analytics(
                access_token=access_token,
                property_ref=ctx.property_ref,
                dimensions=template.dimensions,
                start_date=ctx.window_start,
                end_date=ctx.window_end,
                start_row=start_row,
            )
            wrote = await self._write_artifact(
                ctx, template=template, page=page, start_row=start_row
            )
            if not wrote:
                return False
            if len(page.rows) < page_size:
                return True
            start_row += page_size

    async def _dataset_resume(
        self, run_id: uuid.UUID, dataset: str
    ) -> tuple[int, bool]:
        """Resume offset for one dataset from its DURABLE artifacts.

        A retry never refetches a persisted page (immutability + idempotent
        retries): the artifact pages already written for this run tell us
        either that the dataset is complete (last page was partial) or the
        next ``startRow`` to request.
        """
        page_size = integration_settings.sync_page_size
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        IntegrationImportArtifact.query_snapshot,
                        IntegrationImportArtifact.row_count,
                    ).where(
                        IntegrationImportArtifact.sync_run_id == run_id,
                        IntegrationImportArtifact.dataset == dataset,
                    )
                )
            ).all()
        if not rows:
            return 0, False
        last_start_row = 0
        last_row_count = 0
        for query_snapshot, row_count in rows:
            snapshot_start = int((query_snapshot or {}).get("startRow") or 0)
            if snapshot_start >= last_start_row:
                last_start_row = snapshot_start
                last_row_count = row_count
        if last_row_count < page_size:
            return 0, True
        return last_start_row + page_size, False

    async def _still_owned(self, run_id: uuid.UUID) -> bool:
        async with self._session_factory() as session:
            run = await session.get(IntegrationSyncRun, run_id)
            return (
                run is not None
                and run.lease_owner == self.owner
                and run.status == TASK_STATUS_RUNNING
            )

    async def _write_artifact(
        self,
        ctx: _RunContext,
        *,
        template: IntegrationDatasetTemplate,
        page: _ClientPage,
        start_row: int,
    ) -> bool:
        """Write ONE immutable artifact for one fetched page (owner-gated)."""
        canonical = json.dumps(page.payload, sort_keys=True, separators=(",", ":"))
        encoded = canonical.encode("utf-8")
        if len(encoded) > integration_settings.max_inline_payload_bytes:
            raise _PayloadTooLargeError(
                f"page payload is {len(encoded)} bytes, over the inline cap "
                f"of {integration_settings.max_inline_payload_bytes}"
            )
        payload_hash = hashlib.sha256(encoded).hexdigest()
        now = _utcnow()
        async with self._session_factory() as session:
            run = await session.get(
                IntegrationSyncRun, ctx.run_id, with_for_update=True
            )
            if (
                run is None
                or run.lease_owner != self.owner
                or run.status != TASK_STATUS_RUNNING
            ):
                # Lost lease / cancelled between the fetch and this write:
                # discard the page — a lost lease writes NOTHING.
                await session.commit()  # nothing staged; releases the lock
                return False
            session.add(
                IntegrationImportArtifact(
                    sync_run_id=run.id,
                    connection_id=ctx.connection_id,
                    workspace_id=ctx.workspace_id,
                    provider=ctx.provider,
                    dataset=template.dataset,
                    # The exact credential-free API query (invariant 6).
                    query_snapshot={
                        "api_method": template.api_method,
                        "dataset": template.dataset,
                        "property_ref": ctx.property_ref,
                        "startDate": ctx.window_start.isoformat(),
                        "endDate": ctx.window_end.isoformat(),
                        "dimensions": list(template.dimensions),
                        "metrics": list(template.metrics),
                        "rowLimit": integration_settings.sync_page_size,
                        "startRow": start_row,
                    },
                    payload_hash=payload_hash,
                    fetched_at=now,
                    row_count=len(page.rows),
                    payload=page.payload,
                )
            )
            await session.commit()
            return True

    # --- Terminal accounting -------------------------------------------------

    async def _handle_provider_error(
        self, ctx: _RunContext, exc: GscApiError | Ga4ApiError | BingApiError
    ) -> None:
        if exc.error_code == ERROR_GRANT_AUTH_FAILED:
            await self._mark_grant_needs_reauth(ctx)
            await self._queue.fail(
                task_id=ctx.run_id,
                owner=self.owner,
                error_code=exc.error_code,
                error_detail=str(exc),
            )
            return
        if exc.retryable:
            if ctx.attempt_count < ctx.max_attempts:
                await self._queue.retry(
                    task_id=ctx.run_id,
                    owner=self.owner,
                    delay_seconds=integration_settings.retry_delay(
                        ctx.attempt_count, exc.retry_after_seconds
                    ),
                    error_code=exc.error_code,
                    error_detail=str(exc),
                )
            else:
                await self._queue.fail(
                    task_id=ctx.run_id,
                    owner=self.owner,
                    error_code=INTEGRATION_QUEUE_SPEC.max_attempts_error,
                    error_detail=str(exc),
                )
            return
        await self._queue.fail(
            task_id=ctx.run_id,
            owner=self.owner,
            error_code=exc.error_code,
            error_detail=str(exc),
        )

    async def _finalize_success(self, ctx: _RunContext) -> bool:
        """Derive + C5 projections + events + terminal status in ONE transaction.

        Idempotent under retry: derivation inserts ``ON CONFLICT DO NOTHING``
        and the projection enqueues dedupe on deterministic idempotency keys,
        so a crash before this commit replays to the same terminal state.
        """
        now = _utcnow()
        async with self._session_factory() as session:
            run = await session.get(
                IntegrationSyncRun, ctx.run_id, with_for_update=True
            )
            if (
                run is None
                or run.lease_owner != self.owner
                or run.status != TASK_STATUS_RUNNING
            ):
                await session.commit()  # nothing staged; releases the lock
                return False
            artifacts = list(
                (
                    await session.scalars(
                        select(IntegrationImportArtifact)
                        .where(IntegrationImportArtifact.sync_run_id == run.id)
                        .order_by(
                            IntegrationImportArtifact.created_at.asc(),
                            IntegrationImportArtifact.id.asc(),
                        )
                    )
                ).all()
            )
            connection = await session.get(
                IntegrationConnection, ctx.connection_id, with_for_update=True
            )
            if connection is None:
                # Defensive: the composite FK cascade removes a connection's
                # runs, so reaching here means corrupted state.
                await session.commit()
                return False
            try:
                derived = await derive_run(
                    session, run=run, connection=connection, artifacts=artifacts
                )
            except UnmappedPropertyError as exc:
                # Never guessed (spec section 4): the run fails terminal.
                run.status = TASK_STATUS_FAILED
                run.completed_at = now
                run.error_code = ERROR_UNMAPPED_PROPERTY
                run.error_detail = str(exc)[:2000]
                run.lease_owner = None
                run.lease_expires_at = None
                await session.commit()
                return True
            # C5: post-sync projections are the FINAL derivation step.
            await enqueue_post_sync_projections(
                session,
                project_id=derived.project_id,
                import_artifact_ids=derived.artifact_ids,
            )
            connection.last_synced_at = now
            session.add(
                IntegrationEvent(
                    workspace_id=ctx.workspace_id,
                    connection_id=ctx.connection_id,
                    grant_id=ctx.grant_id,
                    event_type=EVENT_INTEGRATION_SYNC_FINISHED,
                    message=f"Sync finished for {ctx.provider}",
                    payload={
                        "provider": ctx.provider,
                        "sync_run_id": str(ctx.run_id),
                        "sync_kind": ctx.sync_kind,
                        "window_start": ctx.window_start.isoformat(),
                        "window_end": ctx.window_end.isoformat(),
                        "resync_seq": ctx.resync_seq,
                        "project_id": str(derived.project_id),
                        "artifact_ids": [str(a) for a in derived.artifact_ids],
                        "row_count": sum(a.row_count for a in artifacts),
                        "metric_row_count": derived.metric_row_count,
                    },
                )
            )
            run.status = TASK_STATUS_SUCCEEDED
            run.completed_at = now
            run.error_code = ""
            run.error_detail = ""
            run.lease_owner = None
            run.lease_expires_at = None
            await session.commit()
            return True

    async def _heartbeat_loop(
        self, run_id: uuid.UUID
    ) -> None:  # pragma: no cover - timing loop
        interval = max(1.0, integration_settings.heartbeat_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._queue.heartbeat(task_id=run_id, owner=self.owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A dead heartbeat loop silently expires the lease and lets
                # the sweeper hand the run to another worker mid-call; keep
                # beating through transient failures instead.
                logger.exception(
                    "heartbeat failed; retrying", extra={"sync_run_id": str(run_id)}
                )


def main() -> None:  # pragma: no cover - process entrypoint
    configure_logging()
    worker = IntegrationWorker()
    asyncio.run(worker.run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
