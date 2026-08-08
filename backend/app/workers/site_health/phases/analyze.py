"""Phase 2 — ANALYZE: fetch one monitored URL and persist its evidence.

The heart of the crawl. Fetches through the SSRF-safe ladder, extracts bounded
page facts, evaluates the per-page rule catalog, and writes ONE immutable
artifact + attempt rows + page analysis + rule evaluations + issues + scores in
a single transaction gated by a FOR UPDATE owner/liveness re-check. The
queue row is acknowledged OUTSIDE that transaction, and the link-check task for
the URL is enqueued INSIDE it — so the link task can never exist without the
analysis it probes.

Split out of SiteHealthWorker for readability only — see the package
docstring; this is a mixin on the one worker class, not a separate process.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.site_health.page_kinds import classify
from app.analysis.site_health.parser import extract_page_facts
from app.analysis.site_health.rules import RuleEvaluation, evaluate_all
from app.analysis.site_health.scoring import score_analysis
from app.connectors.web_evidence.fetcher import FetchError, FetchRequest
from app.core.config.site_health import (
    ANALYZER_VERSION,
    DISCOVERY_STATUS_COMPLETED,
    ERROR_BOT_BLOCKED,
    EVENT_ANALYSIS_PROGRESS,
    EXTRACTOR_VERSION,
    FETCH_PURPOSE_ANALYZE,
    HTML_CONTENT_TYPES,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    RULE_OUTCOME_FAIL,
    SCORING_VERSION,
    TASK_KIND_LINK_CHECK,
)
from app.domain.site_health.discovery import _enqueue_task as _enqueue_discovery_task
from app.domain.site_health.industry_pack import classify_industry_role
from app.domain.site_health.selection import evaluate_task_guard, lease_is_owned
from app.domain.site_health.state_events import record_crawl_event
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteIssue,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
    SiteUrlObservation,
    WorkspaceSiteHealthRuntime,
)
from app.workers.site_health.helpers import (
    _classify_http_error,
    _count_disclosure,
    _is_bot_block,
    _is_crawl_finalize_rule,
    _robots_denial_error,
    _utcnow,
)
from app.workers.site_health.lifecycle import crawl_root_identity
from app.workers.site_health.outcomes import AnalyzeOutcome as _AnalyzeOutcome
from app.workers.site_health.phases.support import PhaseSupport
from app.workers.site_health.urls import authority_key as _authority_key


class AnalyzePhaseMixin(PhaseSupport):
    """TASK_KIND_ANALYZE handling."""

    async def _run_analyze(self, task_id: uuid.UUID, crawl_id: uuid.UUID) -> None:
        """Fetch + deep-analyze one monitored URL, persisting evidence atomically.

        Mirrors the discover flow: load config in one short session, fetch the
        URL through the SSRF-safe fetcher (heartbeating the lease), parse the
        bounded page facts, then persist ONE immutable artifact + attempt +
        page analysis + rule evaluations + issues + scores in a single
        transaction gated by a ``FOR UPDATE`` owner/liveness re-check. The queue
        row is succeeded / retried / failed OUTSIDE that transaction.
        """
        # If evidence committed but the out-of-transaction queue acknowledgement
        # failed, a reclaimed task must acknowledge that durable result instead
        # of fetching and attempting the unique inserts again.
        persisted_artifact_id = await self._persisted_analysis_artifact_id(task_id)
        if persisted_artifact_id is not None:
            await self._queue.succeed(
                task_id=task_id,
                owner=self.owner,
                result_artifact_id=persisted_artifact_id,
            )
            return

        async with self._session_factory() as session:
            task = await session.get(SiteCrawlTask, task_id)
            crawl = await session.get(SiteCrawl, crawl_id)
            if task is None or crawl is None:
                return
            guard = await self._evaluate_analyze_guard(
                session, task=task, crawl=crawl, lock=False
            )
            if not guard.ok:
                await session.rollback()
                await self._queue.cancel(task_id=task_id)
                return
            requested_url = task.requested_url
            config = dict(crawl.configuration or {})
            root_registrable_domain = config.get("root_registrable_domain") or ""

        # One heartbeat across fetch + persist (see ``_leased``).
        async with self._leased(task_id):
            outcome = await self._fetch_analyze(
                requested_url=requested_url,
                root_registrable_domain=root_registrable_domain,
            )
            await self._persist_analyze(
                task_id=task_id,
                crawl_id=crawl_id,
                requested_url=requested_url,
                outcome=outcome,
            )

    async def _persisted_analysis_artifact_id(
        self, task_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Return durable analyze evidence for an idempotently reclaimed task."""
        async with self._session_factory() as session:
            return await session.scalar(
                select(SiteFetchArtifact.id)
                .join(
                    SitePageAnalysis,
                    SitePageAnalysis.artifact_id == SiteFetchArtifact.id,
                )
                .where(
                    SiteFetchArtifact.task_id == task_id,
                    SiteFetchArtifact.fetch_purpose == FETCH_PURPOSE_ANALYZE,
                    SitePageAnalysis.status == PAGE_ANALYSIS_STATUS_COMPLETED,
                )
                .limit(1)
            )

    async def _evaluate_analyze_guard(
        self,
        session: AsyncSession,
        *,
        task: SiteCrawlTask,
        crawl: SiteCrawl,
        lock: bool,
    ):
        """Evaluate Task 4's live membership/runtime guard from DB rows."""
        monitored_stmt = select(MonitoredSiteUrl).where(
            MonitoredSiteUrl.project_id == crawl.project_id,
            MonitoredSiteUrl.site_url_id == task.site_url_id,
        )
        runtime_stmt = select(WorkspaceSiteHealthRuntime).where(
            WorkspaceSiteHealthRuntime.workspace_id == crawl.workspace_id
        )
        if lock:
            monitored_stmt = monitored_stmt.with_for_update()
            runtime_stmt = runtime_stmt.with_for_update()
        monitored = (await session.execute(monitored_stmt)).scalar_one_or_none()
        runtime = (await session.execute(runtime_stmt)).scalar_one_or_none()
        return evaluate_task_guard(
            crawl=crawl,
            task=task,
            monitored=monitored,
            runtime=runtime,
            owner=self.owner,
        )

    async def _lock_guarded_analyze_task(
        self,
        session: AsyncSession,
        *,
        task_id: uuid.UUID,
        crawl_id: uuid.UUID,
    ) -> tuple[tuple[SiteCrawlTask, SiteCrawl] | None, bool]:
        """Lock live runtime/membership and the owned task before writes.

        The runtime row is the selection flow's serialization point, so lock it
        before membership/task rows to follow that flow's lock order and avoid
        deadlocks with a concurrent monitored-set replacement.

        Returns ``(locked_rows, guard_denied)``. ``guard_denied`` is true only
        while this worker still owns the task but live crawl/membership/
        runtime state blocks analysis; a lost lease is not ours to cancel.
        """
        task_hint = await session.get(SiteCrawlTask, task_id)
        crawl_hint = await session.get(SiteCrawl, crawl_id)
        if task_hint is None or crawl_hint is None:
            return None, False

        runtime = (
            await session.execute(
                select(WorkspaceSiteHealthRuntime)
                .where(
                    WorkspaceSiteHealthRuntime.workspace_id == crawl_hint.workspace_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        monitored = (
            await session.execute(
                select(MonitoredSiteUrl)
                .where(
                    MonitoredSiteUrl.project_id == crawl_hint.project_id,
                    MonitoredSiteUrl.site_url_id == task_hint.site_url_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        # ``populate_existing`` is load-bearing, not defensive. Both rows are
        # already in this session's identity map from the unlocked hint reads
        # above, and a plain ``get(..., with_for_update=True)`` re-SELECTs under
        # the lock but does NOT overwrite attributes that are already loaded —
        # so the caller would go on to read (and increment) the value from
        # BEFORE the lock was taken. That is a textbook lost update:
        # ``crawl.analyzed_url_count += 1`` under concurrent analyze tasks
        # silently dropped increments (observed: a crawl with 7 completed
        # analyses whose counter read 5, which the dashboard then reported as
        # 2 phantom "queued" pages).
        crawl = await session.get(
            SiteCrawl, crawl_id, with_for_update=True, populate_existing=True
        )
        task = await session.get(
            SiteCrawlTask, task_id, with_for_update=True, populate_existing=True
        )
        decision = evaluate_task_guard(
            crawl=crawl,
            task=task,
            monitored=monitored,
            runtime=runtime,
            owner=self.owner,
        )
        if not decision.ok:
            still_owned = lease_is_owned(task, owner=self.owner)
            return None, still_owned
        if task is None or crawl is None:  # unreachable: guard checked both
            return None, False
        return (task, crawl), False

    async def _fetch_analyze(
        self,
        *,
        requested_url: str,
        root_registrable_domain: str,
    ) -> _AnalyzeOutcome:
        """Fetch + parse one monitored URL into a bounded ``_AnalyzeOutcome``.

        Returns parsed page facts on success (2xx), a classified error token on
        an HTTP 4xx/5xx or a ``FetchError``. Never raises for an expected fetch
        failure — the caller records an attempt row either way.

        v2 P2: enforces the per-authority robots.txt policy before fetching —
        a denied URL short-circuits to ``ERROR_ROBOTS_DENIED`` (non-retryable;
        presentation maps it to ``blocked`` via POLICY_BLOCKING_ERROR_CODES).

        A response carrying a challenge-platform marker classifies as terminal
        ``ERROR_BOT_BLOCKED`` (presentation: ``blocked``).
        """
        authority = _authority_key(requested_url)
        if authority:
            policy, _, _ = await self._ensure_robots_policy(authority)
            if not policy.can_fetch(requested_url):
                error_code, error_detail = _robots_denial_error(policy)
                return _AnalyzeOutcome(
                    error_code=error_code,
                    error_detail=error_detail,
                    retryable=False,
                )
        request = FetchRequest(
            url=requested_url,
            purpose=FETCH_PURPOSE_ANALYZE,
            allowed_content_types=HTML_CONTENT_TYPES,
        )
        started = time.monotonic()
        try:
            async with self._new_fetcher() as fetcher:
                result = await fetcher.fetch(
                    request,
                    root_registrable_domain=root_registrable_domain or None,
                    enforce_scope=False,
                )
        except FetchError as exc:
            latency = int((time.monotonic() - started) * 1000)
            return _AnalyzeOutcome(
                error_code=exc.error_code,
                error_detail=str(exc),
                retryable=exc.retryable,
                latency_ms=latency,
                status_code=exc.status_code,
                retry_after_seconds=exc.retry_after_seconds,
                attempts=exc.attempts,
            )

        status = result.status_code
        # A challenge marker -> terminal ERROR_BOT_BLOCKED (see
        # ``_fetch_discover``); checked before status classification.
        if _is_bot_block(result):
            return _AnalyzeOutcome(
                result=result,
                error_code=ERROR_BOT_BLOCKED,
                retryable=False,
                latency_ms=result.latency_ms,
                status_code=status,
                attempts=result.attempts,
            )
        classified = _classify_http_error(status)
        if classified is not None:
            error_code, retryable = classified
            return _AnalyzeOutcome(
                result=result,
                error_code=error_code,
                retryable=retryable,
                latency_ms=result.latency_ms,
                status_code=status,
                attempts=result.attempts,
            )

        facts = extract_page_facts(
            result.body,
            final_url=result.final_url or requested_url,
            content_type=result.content_type,
            charset=result.charset,
            status_code=status,
            redacted_headers=result.redacted_headers,
            http_version=result.http_version,
            ttfb_ms=result.ttfb_ms,
            latency_ms=result.latency_ms,
            wire_bytes=result.wire_bytes,
            decoded_bytes=result.decoded_bytes,
        )
        return _AnalyzeOutcome(
            result=result,
            facts=facts,
            status_code=status,
            latency_ms=result.latency_ms,
            attempts=result.attempts,
        )

    async def _persist_analyze(
        self,
        *,
        task_id: uuid.UUID,
        crawl_id: uuid.UUID,
        requested_url: str,
        outcome: _AnalyzeOutcome,
    ) -> None:
        """Persist the analyze result atomically, then finalize the queue row."""
        should_retry = False
        retry_attempt = 0
        succeeded_artifact_id: uuid.UUID | None = None
        guard_denied = False
        async with self._session_factory() as session:
            locked, guard_denied = await self._lock_guarded_analyze_task(
                session, task_id=task_id, crawl_id=crawl_id
            )
            if locked is None:
                await session.rollback()
                if not guard_denied:
                    return
            else:
                task, crawl = locked
                artifact_id: uuid.UUID | None = None
                if outcome.facts is not None and outcome.result is not None:
                    artifact_id = await self._write_artifact(
                        session,
                        crawl=crawl,
                        task=task,
                        result=outcome.result,
                        fetch_purpose=FETCH_PURPOSE_ANALYZE,
                        normalized_facts=outcome.facts,
                    )
                    await self._write_page_analysis(
                        session,
                        crawl=crawl,
                        task=task,
                        artifact_id=artifact_id,
                        facts=outcome.facts,
                    )
                    crawl.analyzed_url_count += 1
                    task.result_artifact_id = artifact_id
                    succeeded_artifact_id = artifact_id
                    # Automatically enqueue the link-check task for this URL in
                    # the same transaction as the completed analysis, so the
                    # worker's own ``TASK_KIND_LINK_CHECK`` handling is ever
                    # reached for a normal crawl. Conflict-safe (``ON CONFLICT
                    # DO NOTHING`` on the unique
                    # ``(crawl_id, task_kind, url_hash, generation)`` slot) so
                    # a reclaimed/retried analyze task never double-enqueues.
                    await _enqueue_discovery_task(
                        session,
                        crawl=crawl,
                        site_url_id=task.site_url_id,
                        url=requested_url,
                        url_hash_value=task.url_hash,
                        task_kind=TASK_KIND_LINK_CHECK,
                        depth=task.depth,
                        generation=task.generation,
                        parent_site_url_id=task.parent_site_url_id,
                        phase_run_id=task.phase_run_id,
                    )
                    record_crawl_event(
                        session,
                        crawl_id=crawl_id,
                        event_type=EVENT_ANALYSIS_PROGRESS,
                        message="analysis progress",
                        payload={"analyzed": crawl.analyzed_url_count},
                        count_disclosure=_count_disclosure(crawl),
                    )
                else:
                    exhausted = task.attempt_count + 1 >= task.max_attempts
                    should_retry = outcome.retryable and not exhausted
                    retry_attempt = task.attempt_count + 1

                self._write_attempt(
                    session,
                    crawl=crawl,
                    task=task,
                    outcome=outcome,
                    succeeded=outcome.facts is not None,
                    requested_url=requested_url,
                    artifact_id=artifact_id,
                )
                task.attempt_count += 1
                await session.commit()

        if guard_denied:
            await self._queue.cancel(task_id=task_id)
            return

        await self._finalize_queue_row(
            task_id=task_id,
            succeeded=succeeded_artifact_id is not None,
            succeeded_artifact_id=succeeded_artifact_id,
            should_retry=should_retry,
            retry_attempt=retry_attempt,
            error_code=outcome.error_code,
            error_detail=outcome.error_detail,
            retry_after_seconds=outcome.retry_after_seconds,
        )

    async def _write_page_analysis(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
        artifact_id: uuid.UUID,
        facts: dict,
    ) -> uuid.UUID:
        """Create the page analysis + rule evaluations + issues + scores.

        One ``SitePageAnalysis`` (unique ``artifact_id``), one
        ``SiteRuleEvaluation`` per rule (unique ``(analysis_id, rule_id)``), a
        ``SiteIssue`` snapshot per FAIL (unique ``evaluation_id``), and the
        deterministic Technical/AEO/overall scores stamped with the versions.
        """
        site_url_id = await self._resolve_analysis_site_url_id(
            session, crawl=crawl, task=task
        )
        # Evaluation-time enrichment goes onto a SHALLOW COPY, never the facts
        # dict the caller handed ``_write_artifact``: that dict IS the artifact's
        # ``normalized_facts``, and the persisted evidence must carry only what
        # the extractor produced (the injected keys below are provenance of this
        # analysis, not of the fetch). Copying makes that independent of insert
        # ordering / JSON-mutation tracking rather than relying on the flush
        # having already serialized the pre-injection value.
        eval_facts = dict(facts)
        # v2 P1: classify the page type and inject it into the facts dict
        # BEFORE rule evaluation, so page_kind applicability tokens, per-type
        # thin-content minimums, and weight overrides resolve against it
        # (spec §5.1 pipeline slot; evaluate_all keeps its pure (facts)
        # signature). The type + classifier version persist on the analysis
        # row for provenance (invariant 4).
        assessment = classify(
            str((facts.get("delivery") or {}).get("final_url") or ""), facts
        )
        eval_facts["page_kind"] = assessment.page_kind
        eval_facts["page_kind_evidence"] = assessment.to_evidence()
        # v2 P2 (spec §5.3): inside the crawl ROOT's own analysis only, inject
        # the crawl's site_facts so site_root-scoped rules (AI-crawler access,
        # llms.txt) evaluate exactly once per crawl, anchored on this analysis.
        # Injected into the copy only, so the persisted normalized_facts
        # deliberately do NOT carry it (same as page_kind).
        if crawl.site_facts:
            _root_canonical, root_hash = crawl_root_identity(crawl)
            if root_hash and root_hash == task.url_hash:
                eval_facts["site"] = crawl.site_facts
        evaluations: list[RuleEvaluation] = [
            ev
            for ev in evaluate_all(eval_facts)
            # The analyze writer NEVER persists crawl_finalize-scoped
            # evaluations (no placeholder not_applicable rows): the unique
            # (analysis_id, rule_id) slot stays free for the finalize pass,
            # which solely owns those rules' rows (single-writer per scope).
            if not _is_crawl_finalize_rule(ev.rule_id)
        ]
        scores = score_analysis(evaluations)
        # Refresh the lightweight identity/observation state from the analyze
        # fetch. A Free sample URL is fetched ONLY by its analyze task (no
        # per-URL discover runs), so its admission-time observation row is
        # sparse (no title/status) until enriched here; without this the pages
        # table shows blank titles for 9 of 10 sampled URLs.
        site_url = await session.get(SiteUrl, site_url_id)
        if site_url is not None:
            title = str(facts.get("title") or "")
            if title:
                site_url.latest_title = title[:1024]
            site_url.latest_content_type = str(facts.get("content_type") or "")[:128]
            site_url.last_seen_crawl_id = crawl.id
            site_url.discovery_status = DISCOVERY_STATUS_COMPLETED
        observation = await session.scalar(
            select(SiteUrlObservation).where(
                SiteUrlObservation.crawl_id == crawl.id,
                SiteUrlObservation.site_url_id == site_url_id,
            )
        )
        if observation is not None and observation.status_code is None:
            # ``status_code``/``final_url`` are nested under ``delivery`` by the
            # parser (only ``content_type``/``title`` are top level). Reading
            # them off the root left every observation with a NULL status and a
            # blank final URL — and because the guard above keys on
            # ``status_code is None``, the block re-ran forever without ever
            # filling it. Same accessor the rule-eval path already uses.
            delivery = facts.get("delivery") or {}
            observation.status_code = delivery.get("status_code")
            observation.final_url = str(delivery.get("final_url") or "")[:2048]
            observation.content_type = str(facts.get("content_type") or "")[:128]
            observation.title = str(facts.get("title") or "")[:1024]
            observation.source_artifact_id = artifact_id
        # Pack-governed industry role, classified from the SAME bounded facts.
        # The pack is compiled once per worker process from the crawl's frozen
        # manifest, so this call performs no I/O, no hashing, and no model call.
        role = classify_industry_role(
            crawl=crawl,
            facts=facts,
            page_kind=assessment.page_kind,
            site_url=site_url,
        )
        analysis = SitePageAnalysis(
            workspace_id=crawl.workspace_id,
            project_id=crawl.project_id,
            crawl_id=crawl.id,
            site_url_id=site_url_id,
            artifact_id=artifact_id,
            status=PAGE_ANALYSIS_STATUS_COMPLETED,
            technical_score=scores.technical_score,
            aeo_score=scores.aeo_score,
            overall_score=scores.overall_score,
            analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
            scoring_version=crawl.scoring_version or SCORING_VERSION,
            page_kind=assessment.page_kind,
            classifier_version=assessment.classifier_version,
            # Persist the bounded classifier evidence with the row (the
            # evaluation-time copy above is never persisted, by design).
            page_kind_evidence=assessment.to_evidence(),
            source_artifact_ids=[artifact_id],
            finalized_at=_utcnow(),
            **role,
        )
        # Append-only: supersede any earlier current understanding of this PAGE
        # before inserting the new one.
        #
        # Matched on the page, not the artifact. A rerun fetches again and gets
        # a NEW artifact, so the artifact-keyed supersede never found the
        # previous analysis and left two live rows for one URL — which
        # ``build_crawl_knowledge`` then folded into one model, manufacturing
        # exactly the contradictions-out-of-a-rerun its docstring warns about.
        #
        # The partial unique index stays keyed on ``artifact_id``: the snapshot
        # deliberately tolerates several current analyses per page and resolves
        # the latest itself, so tightening the constraint is a separate
        # decision from making this supersede actually fire.
        await session.execute(
            update(SitePageAnalysis)
            .where(
                SitePageAnalysis.crawl_id == analysis.crawl_id,
                SitePageAnalysis.site_url_id == analysis.site_url_id,
                SitePageAnalysis.is_current.is_(True),
            )
            .values(is_current=False)
        )
        session.add(analysis)
        await session.flush()

        evaluation_ids: list[uuid.UUID] = []
        for ev in evaluations:
            evaluation = SiteRuleEvaluation(
                workspace_id=crawl.workspace_id,
                analysis_id=analysis.id,
                source_artifact_id=artifact_id,
                rule_id=ev.rule_id,
                dimension=ev.dimension,
                category=ev.category,
                severity=ev.severity,
                weight=ev.weight,
                outcome=ev.outcome,
                evidence=ev.evidence,
                supporting_artifact_ids=[artifact_id],
                extractor_version=crawl.extractor_version or EXTRACTOR_VERSION,
                analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
                rule_version=ev.rule_version,
            )
            session.add(evaluation)
            await session.flush()
            evaluation_ids.append(evaluation.id)
            if ev.outcome == RULE_OUTCOME_FAIL:
                session.add(
                    SiteIssue(
                        workspace_id=crawl.workspace_id,
                        project_id=crawl.project_id,
                        crawl_id=crawl.id,
                        site_url_id=site_url_id,
                        analysis_id=analysis.id,
                        evaluation_id=evaluation.id,
                        source_artifact_id=artifact_id,
                        rule_id=ev.rule_id,
                        dimension=ev.dimension,
                        category=ev.category,
                        severity=ev.severity,
                        evidence=ev.evidence,
                        remediation=ev.remediation,
                        analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
                        rule_version=ev.rule_version,
                    )
                )
        analysis.source_evaluation_ids = evaluation_ids
        return analysis.id

    async def _resolve_analysis_site_url_id(
        self,
        session: AsyncSession,
        *,
        crawl: SiteCrawl,
        task: SiteCrawlTask,
    ) -> uuid.UUID:
        """Resolve the SiteUrl identity for an analyze task's URL.

        Prefers the task's own ``site_url_id`` (set at admission for monitored
        URLs); falls back to a lookup / conflict-safe create keyed on the
        canonical url hash so an analyze task never fails for a missing row.
        """
        if task.site_url_id is not None:
            return task.site_url_id
        resolved = await self._resolve_site_url_id(
            session, crawl=crawl, url=task.requested_url, depth=task.depth
        )
        if resolved is None:
            # Only reachable when the URL cannot be canonicalized at all —
            # admission already canonicalized it, so treat as a hard bug. A
            # retry at depth 0 used to sit here, but ``_resolve_site_url_id``
            # returns None only for an uncanonicalizable URL: depth never
            # affects that, so the retry could not have changed the outcome.
            raise RuntimeError(
                f"could not resolve SiteUrl identity for {task.requested_url!r}"
            )
        return resolved
