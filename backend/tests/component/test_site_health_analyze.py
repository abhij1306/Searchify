"""Analyze phase: the live entitlement/membership guard, evidence persistence, scoring.

Split from the former test_site_health_worker.py monolith; shared setup lives
in ``site_health_worker_helpers``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.site_health import (
    AI_CRAWLER_BOTS,
    ANALYSIS_STATUS_CANCELLED,
    ANALYSIS_STATUS_COMPLETED,
    ANALYSIS_STATUS_FAILED,
    ANALYZER_VERSION,
    AUTOMATIC_MONITOR_LIMIT_KEY,
    CRAWL_STATUS_COMPLETED,
    CRAWL_STATUS_PARTIALLY_COMPLETED,
    CRAWL_STATUS_RUNNING,
    ERROR_ROBOTS_DENIED,
    EXTRACTOR_VERSION,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    PAGE_KIND_PROFILES,
    RULE_CATALOG_VERSION,
    RULE_OUTCOME_FAIL,
    RULE_OUTCOME_NOT_APPLICABLE,
    RULE_OUTCOME_PASS,
    SCORING_VERSION,
    SELECTION_SOURCE_BOOTSTRAP,
    SELECTION_SOURCE_FREE_SAMPLE,
    TASK_KIND_ANALYZE,
    TASK_KIND_DISCOVER,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.site_health.normalization import canonical_identity
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteHealthSnapshot,
    SiteIssue,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
)
from app.workers.site_health_worker import (
    SiteHealthWorker,
)
from tests.component.site_health_helpers import seed_site_crawl
from tests.component.site_health_worker_helpers import (
    DEFAULT_SEED_MONITORED_URLS,
    _analyses_by_page_url,
    _ByteStream,
    _configure_crawl,
    _FakeResolver,
    _html,
    _rich_html,
    _rich_page,
    _seed_analyze_phase_crawl,
    _seed_analyze_ready,
    _seed_runtime,
    _thin_html,
    _worker,
)


@pytest.mark.asyncio
async def test_analyze_guard_blocks_live_entitlement_downgrade_before_io(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed, _site_url_id, task_id = await _seed_analyze_ready(session_factory)
    async with session_factory() as session:
        await _seed_runtime(session, seed.workspace_id, monitored_urls=0)
        await session.commit()

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, stream=_ByteStream(b"unexpected"))

    worker = SiteHealthWorker(
        session_factory=session_factory,
        owner="downgraded",
        resolver=_FakeResolver(),
        transport=httpx.MockTransport(handler),
    )
    await worker.run_until_idle()

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        assert task is not None
        artifact_count = await session.scalar(
            select(func.count())
            .select_from(SiteFetchArtifact)
            .where(SiteFetchArtifact.task_id == task_id)
        )
        assert requests == []
        assert task.status == TASK_STATUS_CANCELLED
        assert artifact_count == 0
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        assert crawl.analysis_status == ANALYSIS_STATUS_CANCELLED
        assert crawl.status == CRAWL_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_cancelled_user_analysis_does_not_penalize_applicable_free_sample(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Mixed cancelled+succeeded work is complete over applicable coverage."""
    seed, _user_site_url_id, user_task_id = await _seed_analyze_ready(session_factory)
    sample_url = "https://example.com/sample"
    canonical, sample_hash = canonical_identity(sample_url)
    async with session_factory() as session:
        sample_site_url = SiteUrl(
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            normalized_url=canonical,
            url_hash=sample_hash,
            display_url=canonical,
            host="example.com",
            depth=0,
        )
        session.add(sample_site_url)
        await session.flush()
        session.add(
            MonitoredSiteUrl(
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                profile_id=seed.profile_id,
                site_url_id=sample_site_url.id,
                active=True,
                selection_source=SELECTION_SOURCE_FREE_SAMPLE,
            )
        )
        sample_task = SiteCrawlTask(
            crawl_id=seed.crawl_id,
            workspace_id=seed.workspace_id,
            site_url_id=sample_site_url.id,
            task_kind=TASK_KIND_ANALYZE,
            requested_url=sample_url,
            url_hash=sample_hash,
            generation=0,
            idempotency_key=f"{seed.crawl_id}:analyze:{sample_hash}:0",
            status=TASK_STATUS_QUEUED,
            priority=1,
            randomized_position=1,
        )
        session.add(sample_task)
        await _seed_runtime(session, seed.workspace_id, monitored_urls=0)
        await session.commit()
        sample_task_id = sample_task.id

    worker = _worker(
        session_factory,
        {"/sample": _rich_html()},
        owner="mixed-applicability",
    )
    await worker.run_until_idle()

    async with session_factory() as session:
        user_task = await session.get(SiteCrawlTask, user_task_id)
        assert user_task is not None
        _sample_task = await session.get(SiteCrawlTask, sample_task_id)
        assert _sample_task is not None
        sample_task = _sample_task
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        snapshot = (
            await session.execute(
                select(SiteHealthSnapshot).where(
                    SiteHealthSnapshot.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert user_task.status == TASK_STATUS_CANCELLED
        assert sample_task.status == TASK_STATUS_SUCCEEDED
        assert crawl.analysis_status == ANALYSIS_STATUS_COMPLETED
        assert crawl.status == CRAWL_STATUS_COMPLETED
        assert snapshot.analyzed_url_count == 1
        assert snapshot.overall_score is not None


@pytest.mark.asyncio
async def test_analyze_guard_discards_result_when_membership_removed_mid_fetch(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, site_url_id, task_id = await _seed_analyze_ready(session_factory)
    worker = _worker(
        session_factory,
        {"/rich": _rich_html()},
        owner="removed-mid-fetch",
    )
    original_fetch = worker._fetch_analyze
    fetched = False

    async def fetch_then_remove(**kwargs):
        nonlocal fetched
        outcome = await original_fetch(**kwargs)
        fetched = True
        async with session_factory() as session:
            await session.execute(
                update(MonitoredSiteUrl)
                .where(
                    MonitoredSiteUrl.workspace_id == seed.workspace_id,
                    MonitoredSiteUrl.site_url_id == site_url_id,
                )
                .values(active=False)
            )
            await session.commit()
        return outcome

    monkeypatch.setattr(worker, "_fetch_analyze", fetch_then_remove)
    await worker.run_until_idle()

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        assert task is not None
        artifact_count = await session.scalar(
            select(func.count())
            .select_from(SiteFetchArtifact)
            .where(SiteFetchArtifact.task_id == task_id)
        )
        analysis_count = await session.scalar(
            select(func.count())
            .select_from(SitePageAnalysis)
            .where(SitePageAnalysis.crawl_id == seed.crawl_id)
        )
        assert fetched is True
        assert task.status == TASK_STATUS_CANCELLED
        assert artifact_count == 0
        assert analysis_count == 0
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        assert crawl.analysis_status == ANALYSIS_STATUS_CANCELLED
        assert crawl.status == CRAWL_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_reclaimed_analyze_acknowledges_already_persisted_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, _site_url_id, task_id = await _seed_analyze_ready(session_factory)
    first = _worker(
        session_factory,
        {"/rich": _rich_html()},
        owner="ack-fails",
    )

    async def drop_queue_ack(**_kwargs) -> None:
        return None

    monkeypatch.setattr(first, "_finalize_queue_row", drop_queue_ack)
    assert await first.run_once() == 1

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        assert task is not None
        assert task.status == TASK_STATUS_RUNNING
        await session.execute(
            update(SiteCrawlTask)
            .where(SiteCrawlTask.id == task_id)
            .values(
                status=TASK_STATUS_QUEUED,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        await session.commit()

    requests: list[httpx.Request] = []

    def should_not_refetch(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, stream=_ByteStream(b"unexpected"))

    reclaimed = SiteHealthWorker(
        session_factory=session_factory,
        owner="reclaimed",
        resolver=_FakeResolver(),
        transport=httpx.MockTransport(should_not_refetch),
    )
    await reclaimed.run_until_idle()

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        assert task is not None
        artifacts = await session.scalar(
            select(func.count())
            .select_from(SiteFetchArtifact)
            .where(SiteFetchArtifact.task_id == task_id)
        )
        analyses = await session.scalar(
            select(func.count())
            .select_from(SitePageAnalysis)
            .where(SitePageAnalysis.crawl_id == seed.crawl_id)
        )
        assert task.status == TASK_STATUS_SUCCEEDED
        assert artifacts == 1
        assert analyses == 1
        # The reclaimed analyze task itself must never refetch its own
        # target: only its automatically-enqueued ``link_check`` task (a
        # legitimate, separate task) may generate requests, and even those
        # target link probes, never a GET of the analyze target itself.
        assert not any(
            req.method == "GET" and req.url.path == "/rich" for req in requests
        )


@pytest.mark.asyncio
async def test_analyze_task_persists_analysis_evaluations_issues_scores(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed, site_url_id, _task_id = await _seed_analyze_ready(session_factory)
    # /other is served too so the rich page's internal link checks out
    # reachable (otherwise the finalize pass's broken_internal_link rule
    # correctly fails + snapshots an issue).
    pages = {"/rich": _rich_page(), "/other": _rich_html()}
    worker = _worker(session_factory, pages, owner="analyze-rich")
    await worker.run_until_idle()

    async with session_factory() as session:
        analysis = (
            await session.execute(
                select(SitePageAnalysis).where(
                    SitePageAnalysis.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert analysis.status == PAGE_ANALYSIS_STATUS_COMPLETED
        assert analysis.overall_score is not None
        assert analysis.technical_score is not None
        assert analysis.aeo_score is not None
        assert analysis.site_url_id == site_url_id

        eval_count = await session.scalar(
            select(func.count())
            .select_from(SiteRuleEvaluation)
            .where(SiteRuleEvaluation.analysis_id == analysis.id)
        )
        # 30 per-page evaluations from the analyze writer + 3 crawl_finalize
        # evaluations from the finalize pass (broken_internal_link,
        # sitemap_orphan, hreflang_conflict), which ran when the crawl
        # terminalized.
        assert eval_count == 33

        # A rich page passes every rule, so no issues are snapshotted.
        issue_count = await session.scalar(
            select(func.count())
            .select_from(SiteIssue)
            .where(SiteIssue.crawl_id == seed.crawl_id)
        )
        assert issue_count == 0

        # An immutable artifact carries the normalized facts (no raw body).
        artifact = await session.get(SiteFetchArtifact, analysis.artifact_id)
        assert artifact is not None
        assert artifact.normalized_facts is not None
        assert (
            artifact.normalized_facts.get("title")
            == "Rich Page - everything about Acme widgets"
        )

        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        assert crawl.status == CRAWL_STATUS_COMPLETED
        assert crawl.analysis_status == ANALYSIS_STATUS_COMPLETED
        assert crawl.analyzed_url_count == 1

        snapshot = (
            await session.execute(
                select(SiteHealthSnapshot).where(
                    SiteHealthSnapshot.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert snapshot.analyzed_url_count == 1
        assert snapshot.overall_score is not None
        assert snapshot.issue_count == issue_count


@pytest.mark.asyncio
async def test_analyze_persists_page_kind_classifier_and_v2_versions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """v2 P1: the analyze task classifies the page, injects page_kind into
    the facts before rule evaluation, and stamps the P1 versions on the
    persisted rows (sh-analyzer-2 / sh-scoring-2 / sh-classifier-2)."""
    from app.core.config.site_health import (
        CLASSIFIER_VERSION,
    )

    assert (ANALYZER_VERSION, SCORING_VERSION, CLASSIFIER_VERSION) == (
        "sh-analyzer-2",
        "sh-scoring-2",
        "sh-classifier-2",
    )

    seed, _site_url_id, _task_id = await _seed_analyze_ready(
        session_factory, root="https://example.com/blog/post-1"
    )
    pages = {"/blog/post-1": _rich_html()}
    worker = _worker(session_factory, pages, owner="analyze-p1")
    await worker.run_until_idle()

    async with session_factory() as session:
        analysis = (
            await session.execute(
                select(SitePageAnalysis).where(
                    SitePageAnalysis.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        # The /blog/ path pattern classified the page as an article.
        assert analysis.page_kind == "article"
        assert analysis.classifier_version == "sh-classifier-2"
        assert analysis.analyzer_version == "sh-analyzer-2"
        assert analysis.scoring_version == "sh-scoring-2"

        # The bounded classifier evidence persisted WITH the row (it used to
        # be computed, injected into the facts dict after the artifact flush,
        # and dropped). Its classifier_version matches the row's stamp.
        evidence = analysis.page_kind_evidence
        assert evidence is not None
        assert evidence["classifier_version"] == analysis.classifier_version
        assert evidence["classified_by"] == "path_pattern"
        assert evidence["confidence"] >= evidence["confidence_threshold"]
        assert evidence["signals"][0]["signal"] == "path_pattern"
        assert evidence["signals"][0]["page_kind"] == "article"

        # facts["page_kind"] reached rule evaluation: the thin-content check
        # read the per-type (article) minimum, not the v1 global.
        thin = (
            await session.execute(
                select(SiteRuleEvaluation).where(
                    SiteRuleEvaluation.analysis_id == analysis.id,
                    SiteRuleEvaluation.rule_id == "technical.thin_content",
                )
            )
        ).scalar_one()
        article_min = PAGE_KIND_PROFILES["article"].min_sufficient_words
        assert thin.evidence["page_kind"] == "article"
        assert thin.evidence["minimum"] == article_min
        # The rich page (140 words) is thin FOR AN ARTICLE (>= 300 words).
        assert thin.outcome == RULE_OUTCOME_FAIL

        # The crawl rollup carries the per-page-type breakdown.
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        summary = crawl.score_summary or {}
        assert summary.get("scoring_version") == "sh-scoring-2"
        by_page_kind = summary.get("by_page_kind") or {}
        assert set(by_page_kind) == {"article"}
        assert by_page_kind["article"]["analyzed_count"] == 1
        assert by_page_kind["article"]["overall_score"] is not None


@pytest.mark.asyncio
async def test_thin_page_generates_multiple_issues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed, _site_url_id, _task_id = await _seed_analyze_ready(
        session_factory, root="https://example.com/thin"
    )
    pages = {"/thin": _thin_html()}
    worker = _worker(session_factory, pages, owner="analyze-thin")
    await worker.run_until_idle()

    async with session_factory() as session:
        issues = (
            (
                await session.execute(
                    select(SiteIssue.rule_id).where(SiteIssue.crawl_id == seed.crawl_id)
                )
            )
            .scalars()
            .all()
        )
        # Thin page fails: meta description, canonical, https, single h1,
        # structured data, open graph, thin content.
        assert "technical.meta_description_present" in issues
        assert "technical.canonical_present" in issues
        assert "technical.thin_content" in issues
        assert len(issues) >= 5


@pytest.mark.asyncio
async def test_analyze_robots_denied_fails_task_without_page_fetch(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The analyze task enforces robots too: denied URL -> non-retryable
    ``robots_denied`` failure (mapped to ``blocked`` in presentation), no
    page fetch, no analysis row."""
    seed, _site_url_id, task_id = await _seed_analyze_ready(session_factory)
    pages = {"/robots.txt": b"User-agent: *\nDisallow: /\n"}
    requests: list[tuple[str, str]] = []
    worker = _worker(session_factory, pages, owner="p2-adeny", requests=requests)
    await worker.run_until_idle()

    # Only the robots fetch happened — never the denied page.
    assert requests == [("GET", "/robots.txt")]

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        assert task is not None
        assert task.status == TASK_STATUS_FAILED
        assert task.error_code == ERROR_ROBOTS_DENIED

        analysis_count = await session.scalar(
            select(func.count())
            .select_from(SitePageAnalysis)
            .where(SitePageAnalysis.crawl_id == seed.crawl_id)
        )
        assert analysis_count == 0

        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        assert crawl.analysis_status == ANALYSIS_STATUS_FAILED
        assert crawl.status == CRAWL_STATUS_PARTIALLY_COMPLETED


@pytest.mark.asyncio
async def test_analyze_injects_site_facts_on_root_analysis_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``facts["site"]`` is injected ONLY into the crawl root's analysis: the
    site_root rules (AI-crawler stance, llms.txt) evaluate exactly once per
    crawl, anchored there; every other page's rows for them are N/A. The
    injected copy never leaks into the persisted artifact facts."""
    root = "https://example.com/"
    second = "https://example.com/a"
    site_facts = {
        "robots": {
            "fetched": True,
            "url": "https://example.com/robots.txt",
            "status_code": 200,
            "ai_crawlers": {
                **{bot: "allow" for bot in AI_CRAWLER_BOTS},
                "GPTBot": "block",
            },
            "sitemaps": ["https://example.com/sitemap.xml"],
        },
        "llms_txt": {
            "fetched": True,
            "url": "https://example.com/llms.txt",
            "status_code": 200,
            "present": True,
        },
        "sitemap": {"fetched": True, "files": ["https://example.com/sitemap.xml"]},
    }
    async with session_factory() as session:
        seed, _ids = await _seed_analyze_phase_crawl(
            session, root=root, urls=(root, second), site_facts=site_facts
        )

    pages = {"/": _html([]), "/a": _html([])}
    worker = _worker(session_factory, pages, owner="p2-inject")
    await worker.run_until_idle()

    async with session_factory() as session:
        by_url = await _analyses_by_page_url(session, seed)
        assert len(by_url) == 2
        root_analysis = by_url["https://example.com/"]
        other_analysis = by_url["https://example.com/a"]

        async def _eval(rule_id, analysis_id):
            return await session.scalar(
                select(SiteRuleEvaluation).where(
                    SiteRuleEvaluation.analysis_id == analysis_id,
                    SiteRuleEvaluation.rule_id == rule_id,
                )
            )

        # Root: the injected stance blocks GPTBot -> the stance rule FAILS;
        # llms.txt is present -> PASS. Provenance is sh-rules-2.
        stance = await _eval("technical.ai_crawler_access", root_analysis.id)
        assert stance is not None
        assert stance.outcome == RULE_OUTCOME_FAIL
        assert stance.evidence["blocked"] == ["GPTBot"]
        assert stance.rule_version == RULE_CATALOG_VERSION == "sh-rules-2"
        llms = await _eval("aeo.llms_txt_present", root_analysis.id)
        assert llms is not None
        assert llms.outcome == RULE_OUTCOME_PASS

        # Non-root: the same rules are N/A (no injection).
        other_stance = await _eval("technical.ai_crawler_access", other_analysis.id)
        assert other_stance is not None
        assert other_stance.outcome == RULE_OUTCOME_NOT_APPLICABLE
        other_llms = await _eval("aeo.llms_txt_present", other_analysis.id)
        assert other_llms is not None
        assert other_llms.outcome == RULE_OUTCOME_NOT_APPLICABLE

        # The injected site copy never lands in the immutable artifact facts,
        # which DO carry the current extractor stamp + the P2 fields. Compared
        # against the config constant rather than a literal: this assertion is
        # about the artifact carrying the version that produced it, not about
        # any particular version number.
        artifact = await session.get(SiteFetchArtifact, root_analysis.artifact_id)
        assert artifact is not None
        facts = artifact.normalized_facts or {}
        assert "site" not in facts
        assert facts.get("extractor_version") == EXTRACTOR_VERSION
        for key in (
            "author",
            "dates",
            "outbound_domains",
            "landmarks",
            "question_heading_ratio",
            "expand_gated_ratio",
            "hreflang_alternates",
            "first_answer_text",
            "inline_script_chars",
        ):
            assert key in facts, key


@pytest.mark.asyncio
async def test_rerun_from_completed_crawl_worker_analyzes_only_reran_url(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Handoff finding 1: a rerun from a COMPLETED crawl runs on a new crawl.

    The domain mints a fresh single-page rerun crawl (no discover root task);
    the worker must analyze ONLY the reran URL and must never re-crawl the site
    (no discover fetch of the root).
    """
    from app.domain.site_health.selection import rerun_page

    source_url = "https://example.com/rich"
    seed, site_url_id, analyze_task_id = await _seed_analyze_ready(
        session_factory, root=source_url
    )

    # Drive the source crawl to a terminal (COMPLETED) state with the URL
    # already analyzed, mirroring the "re-audit from a completed crawl" case.
    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        crawl.status = CRAWL_STATUS_COMPLETED
        crawl.analysis_status = ANALYSIS_STATUS_COMPLETED
        # The seeded analyze task is already accounted for by the source crawl.
        await session.execute(
            update(SiteCrawlTask)
            .where(SiteCrawlTask.id == analyze_task_id)
            .values(status=TASK_STATUS_SUCCEEDED)
        )
        await session.commit()

    # Invoke the domain rerun (what the API endpoint calls). Because there is
    # no active crawl, it mints a fresh rerun crawl.
    async with session_factory() as session:
        from app.domain.site_health.entitlements import resolve_runtime

        runtime = await resolve_runtime(session, seed.workspace_id)
        assert runtime.monitored_url_limit == DEFAULT_SEED_MONITORED_URLS
        result = await rerun_page(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            site_url_id=site_url_id,
        )
        await session.commit()

    assert result.created_new_crawl is True
    new_crawl_id = result.crawl_id
    assert new_crawl_id != seed.crawl_id

    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/rich":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                stream=_ByteStream(_rich_html()),
            )
        return httpx.Response(404, stream=_ByteStream(b""))

    worker = SiteHealthWorker(
        session_factory=session_factory,
        owner="rerun-worker",
        resolver=_FakeResolver(),
        transport=httpx.MockTransport(handler),
    )
    await worker.run_until_idle()

    async with session_factory() as session:
        # The new crawl's analyze task ran and produced a completed analysis
        # for the reran URL.
        tasks = (
            (
                await session.execute(
                    select(SiteCrawlTask).where(SiteCrawlTask.crawl_id == new_crawl_id)
                )
            )
            .scalars()
            .all()
        )
        analyze_tasks = [t for t in tasks if t.task_kind == TASK_KIND_ANALYZE]
        discover_tasks = [t for t in tasks if t.task_kind == TASK_KIND_DISCOVER]
        # No discover task at all -> the site is never re-crawled.
        assert discover_tasks == []
        assert len(analyze_tasks) == 1
        assert analyze_tasks[0].status == TASK_STATUS_SUCCEEDED
        assert analyze_tasks[0].site_url_id == site_url_id

        analyses = (
            (
                await session.execute(
                    select(SitePageAnalysis).where(
                        SitePageAnalysis.crawl_id == new_crawl_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(analyses) == 1
        assert analyses[0].site_url_id == site_url_id
        assert analyses[0].status == ANALYSIS_STATUS_COMPLETED

        new_crawl = await session.get(SiteCrawl, new_crawl_id)
        assert new_crawl is not None
        # The rerun crawl terminalizes cleanly.
        assert new_crawl.status in (
            CRAWL_STATUS_COMPLETED,
            CRAWL_STATUS_RUNNING,
        )

    # The worker performed exactly three GETs — the robots.txt policy fetch
    # (v2 P2: analyze honors robots), the analyze fetch of the reran URL,
    # and the robots.txt policy fetch for the EXTERNAL link-check probe
    # target's authority (link probes honor robots too). It never re-crawled
    # the site (no discover of the root and no other GET). The only other
    # requests are the analyze task's auto-enqueued link-check HEAD probes of
    # the page's referenced links, which are legitimate and target external
    # link URLs, not a site re-crawl.
    gets = [path for method, path in requests if method == "GET"]
    assert gets == ["/robots.txt", "/rich", "/robots.txt"]


@pytest.mark.asyncio
async def test_sample_recrawl_allowance_only_decrements_on_new_activation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Handoff finding 2: a recrawl must not consume allowance for a membership
    that is already active, and must reactivate an inactive one.

    - Re-observing an ALREADY-active free_sample URL is a no-op (no new
      membership, no allowance consumed).
    - Re-observing a DEACTIVATED free_sample URL reactivates it in place and
      consumes exactly one allowance unit.
    """
    from app.domain.site_health.discovery import admit_candidates
    from app.domain.site_health.schemas import FrontierCandidate

    async def _sample_count(session, workspace_id) -> int:
        return await session.scalar(
            select(func.count())
            .select_from(MonitoredSiteUrl)
            .where(
                MonitoredSiteUrl.workspace_id == workspace_id,
                MonitoredSiteUrl.active.is_(True),
                MonitoredSiteUrl.selection_source == SELECTION_SOURCE_FREE_SAMPLE,
            )
        )

    root = "https://example.com/"
    async with session_factory() as session:
        seed = await seed_site_crawl(session, task_count=0, root_url=root)
        await _seed_runtime(session, seed.workspace_id, monitored_urls=0)
        await session.commit()
        await _configure_crawl(
            session,
            crawl_id=seed.crawl_id,
            sample_mode=True,
            count_disclosure=False,
        )

    url = "https://example.com/page"
    canonical, url_hash = canonical_identity(url)
    candidate = FrontierCandidate(
        url=canonical,
        url_hash=url_hash,
        depth=1,
        source_kind="link",
        parent_position=0,
        link_ordinal=0,
    )

    # First admission: a brand-new free_sample membership is activated and the
    # allowance is consumed (one active sample row).
    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        await admit_candidates(session, crawl=crawl, candidates=[candidate])
        await session.commit()
        assert await _sample_count(session, seed.workspace_id) == 1

    # Second admission of the SAME, still-active URL: no new membership, count
    # unchanged (the WHERE-guarded upsert is a no-op, no decrement).
    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        await admit_candidates(session, crawl=crawl, candidates=[candidate])
        await session.commit()
        assert await _sample_count(session, seed.workspace_id) == 1
        total_rows = await session.scalar(
            select(func.count())
            .select_from(MonitoredSiteUrl)
            .where(MonitoredSiteUrl.workspace_id == seed.workspace_id)
        )
        assert total_rows == 1

    # Deactivate the membership (as a selection replacement / deselect would).
    async with session_factory() as session:
        await session.execute(
            update(MonitoredSiteUrl)
            .where(MonitoredSiteUrl.workspace_id == seed.workspace_id)
            .values(active=False, deselected_at=datetime.now(UTC))
        )
        await session.commit()
        assert await _sample_count(session, seed.workspace_id) == 0

    # Re-admission of the now-INACTIVE URL: reactivate in place (same row) and
    # consume one allowance unit again.
    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        await admit_candidates(session, crawl=crawl, candidates=[candidate])
        await session.commit()
        assert await _sample_count(session, seed.workspace_id) == 1
        membership = await session.scalar(
            select(MonitoredSiteUrl).where(
                MonitoredSiteUrl.workspace_id == seed.workspace_id
            )
        )
        assert membership is not None
        assert membership.active is True
        assert membership.deselected_at is None
        assert membership.selection_source == SELECTION_SOURCE_FREE_SAMPLE
        # Still exactly one row: reactivation happened IN PLACE, never a dup.
        total_rows = await session.scalar(
            select(func.count())
            .select_from(MonitoredSiteUrl)
            .where(MonitoredSiteUrl.workspace_id == seed.workspace_id)
        )
        assert total_rows == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_mode", [False, True])
async def test_onboarding_auto_analysis_stops_at_ten_across_batches(
    session_factory: async_sessionmaker[AsyncSession],
    sample_mode: bool,
) -> None:
    from app.domain.site_health.discovery import add_automatic_root, admit_candidates
    from app.domain.site_health.schemas import FrontierCandidate

    async with session_factory() as session:
        seed = await seed_site_crawl(
            session, task_count=1, root_url="https://example.com/"
        )
        await _seed_runtime(session, seed.workspace_id, monitored_urls=50)
        await session.commit()
        await _configure_crawl(
            session,
            crawl_id=seed.crawl_id,
            sample_mode=sample_mode,
            count_disclosure=True,
        )
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl is not None
        crawl.configuration = {
            **crawl.configuration,
            AUTOMATIC_MONITOR_LIMIT_KEY: 10,
            "requested_page_limit": 10,
        }
        await add_automatic_root(session, crawl)
        await session.commit()

    for batch_start in (0, 12):
        candidates = []
        for index in range(batch_start, batch_start + 12):
            url, url_hash = canonical_identity(f"https://example.com/page-{index}")
            candidates.append(
                FrontierCandidate(
                    url=url,
                    url_hash=url_hash,
                    depth=1,
                    source_kind="link",
                    parent_position=0,
                    link_ordinal=index,
                )
            )
        async with session_factory() as session:
            crawl = await session.get(SiteCrawl, seed.crawl_id)
            assert crawl is not None
            await admit_candidates(session, crawl=crawl, candidates=candidates)
            await session.commit()

    async with session_factory() as session:
        analyze_tasks = await session.scalar(
            select(func.count())
            .select_from(SiteCrawlTask)
            .where(
                SiteCrawlTask.crawl_id == seed.crawl_id,
                SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
            )
        )
        memberships = await session.scalar(
            select(func.count())
            .select_from(MonitoredSiteUrl)
            .where(
                MonitoredSiteUrl.project_id == seed.project_id,
                MonitoredSiteUrl.active.is_(True),
                MonitoredSiteUrl.selection_source == SELECTION_SOURCE_BOOTSTRAP,
            )
        )
    assert analyze_tasks == memberships == 10
