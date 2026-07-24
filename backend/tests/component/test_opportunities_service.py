"""Component tests for the opportunities recompute service + projections.

Runs against a real (throwaway) Postgres schema via the shared fixtures: the
recompute write path (supersede-not-mutate, per-project advisory lock, the
partial unique live-target index) and the keyset-paginated read projections
can only be verified against a real database.

Seed scenario (per test): one workspace/project with an owned domain, two
prompts, one completed audit (prompt 0 has NO owned citation and a Globex
competitor signal -> both visibility rules fire; prompt 1 has an owned
citation -> no hit), a MetricSnapshot, and one completed site crawl with two
mapped issues (structured-data + thin-content) plus one unmapped issue.

Expected scores (config formula: severity * value * gap * 10):
- brand_absent_high_value_prompt: high 3.0 * purchase 2.0 * gap 2.0 * 10 = 120.0
- owned_page_not_cited:           medium 2.0 * purchase 2.0 * gap 2.0 * 10 = 80.0
- missing_structured_data:        medium 2.0 * 1.0 * 1.0 * 10 = 20.0
- thin_content:                   low 1.0 * 1.0 * 1.0 * 10 = 10.0 (floor edge)
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.audits import AUDIT_STATUS_COMPLETED
from app.core.config.opportunities import (
    ANALYZER_VERSION,
    CODE_OPPORTUNITY_SUPERSEDED,
    FORMULA_VERSION,
    OPPORTUNITY_RULES_BY_ID,
    RULE_VERSION,
)
from app.core.config.site_health import (
    CRAWL_STATUS_COMPLETED,
    INITIAL_TASK_GENERATION,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    RULE_OUTCOME_FAIL,
    TASK_KIND_ANALYZE,
)
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
from app.domain.opportunities import service
from app.domain.opportunities.service import (
    InvalidCursorError,
    OpportunityNotFoundError,
    OpportunitySupersededError,
    OpportunityValidationError,
)
from app.models.analysis import (
    Citation,
    CompetitorMention,
    MetricSnapshot,
    ResponseAnalysis,
)
from app.models.audit import (
    Audit,
    AuditEngineSnapshot,
    AuditPromptSnapshot,
    AuditTask,
)
from app.models.brand import OwnedDomain
from app.models.opportunity import Opportunity, OpportunitySnapshot
from app.models.project import Project
from app.models.prompt import Prompt, PromptSet
from app.models.site_health import (
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteHealthProfile,
    SiteIssue,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

pytestmark = pytest.mark.asyncio

ROOT_URL = "https://acme.test/"
URL_A = "https://acme.test/a"
URL_B = "https://acme.test/b"
URL_C = "https://acme.test/c"

SCORE_BRAND_ABSENT = 120.0
SCORE_OWNED_PAGE = 80.0
SCORE_STRUCTURED_DATA = 20.0
SCORE_THIN_CONTENT = 10.0


@dataclass
class Scenario:
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    prompt0_id: uuid.UUID
    prompt1_id: uuid.UUID
    audit_id: uuid.UUID
    analysis0_id: uuid.UUID
    analysis1_id: uuid.UUID
    metric_snapshot_id: uuid.UUID
    crawl_id: uuid.UUID
    issue_structured_id: uuid.UUID
    issue_thin_id: uuid.UUID


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:64]


async def _seed_base(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    workspace = Workspace(name="Opp WS")
    session.add(workspace)
    await session.flush()
    user = User(email=f"user-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    )
    project = Project(
        workspace_id=workspace.id,
        name="Acme Visibility",
        brand_name="Acme Corp",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="consumer_like",
        default_repetitions=1,
        website_url=ROOT_URL,
    )
    session.add(project)
    await session.flush()
    session.add(OwnedDomain(project_id=project.id, domain="acme.com"))
    prompt_set = PromptSet(project_id=project.id, name="Default")
    session.add(prompt_set)
    await session.flush()
    prompt_ids: list[uuid.UUID] = []
    for text, intent in (
        ("best crm for small teams", "purchase"),
        ("what is a crm", "discovery"),
    ):
        prompt = Prompt(
            prompt_set_id=prompt_set.id,
            text=text,
            theme="crm",
            intent=intent,
            enabled=True,
            origin="manual",
        )
        session.add(prompt)
        await session.flush()
        prompt_ids.append(prompt.id)
    return workspace.id, project.id, prompt_ids


async def _add_analysis(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    audit: Audit,
    snapshot: AuditPromptSnapshot,
    engine_snapshot: AuditEngineSnapshot,
    prompt_index: int,
    owned_citation: bool,
    competitor: str | None,
) -> ResponseAnalysis:
    task = AuditTask(
        audit_id=audit.id,
        workspace_id=workspace_id,
        prompt_snapshot_id=snapshot.id,
        engine_snapshot_id=engine_snapshot.id,
        prompt_index=prompt_index,
        repetition=0,
        logical_engine="gemini",
        transport_provider="google",
        transport_model="gemini-flash-latest",
        prompt_text=snapshot.text,
        idempotency_key=f"{audit.id}:{prompt_index}:0:gemini",
    )
    session.add(task)
    await session.flush()
    analysis = ResponseAnalysis(
        workspace_id=workspace_id,
        audit_id=audit.id,
        task_id=task.id,
        analyzer_version="b6-analysis-1",
        scoring_rule_version="scoring-v1",
        logical_engine="gemini",
        transport_provider="google",
        transport_model="gemini-flash-latest",
        prompt_index=prompt_index,
        repetition=0,
    )
    session.add(analysis)
    await session.flush()
    if owned_citation:
        session.add(
            Citation(
                workspace_id=workspace_id,
                audit_id=audit.id,
                analysis_id=analysis.id,
                analyzer_version="b6-analysis-1",
                ordinal=0,
                url="https://acme.com/guide",
                title="Acme guide",
                domain="acme.com",
                classification="owned",
                is_owned=True,
            )
        )
    if competitor is not None:
        session.add(
            Citation(
                workspace_id=workspace_id,
                audit_id=audit.id,
                analysis_id=analysis.id,
                analyzer_version="b6-analysis-1",
                ordinal=1,
                url="https://globex.com/crm",
                title="Globex CRM",
                domain="globex.com",
                classification="competitor",
                matched_competitor=competitor,
            )
        )
        session.add(
            CompetitorMention(
                workspace_id=workspace_id,
                audit_id=audit.id,
                analysis_id=analysis.id,
                analyzer_version="b6-analysis-1",
                competitor_name=competitor,
            )
        )
    await session.flush()
    return analysis


async def _add_visibility(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    prompt_ids: list[uuid.UUID],
    with_metric_snapshot: bool = True,
) -> tuple[Audit, ResponseAnalysis, ResponseAnalysis, MetricSnapshot | None]:
    audit = Audit(
        workspace_id=workspace_id,
        project_id=project_id,
        status=AUDIT_STATUS_COMPLETED,
        completed_at=datetime.now(UTC),
        requested_count=2,
        completed_count=2,
    )
    session.add(audit)
    await session.flush()
    snapshots: list[AuditPromptSnapshot] = []
    for index, prompt_id in enumerate(prompt_ids):
        snapshot = AuditPromptSnapshot(
            audit_id=audit.id,
            prompt_id=prompt_id,
            prompt_index=index,
            text="best crm for small teams" if index == 0 else "what is a crm",
            theme="crm",
            intent="purchase" if index == 0 else "discovery",
        )
        session.add(snapshot)
        await session.flush()
        snapshots.append(snapshot)
    engine_snapshot = AuditEngineSnapshot(
        audit_id=audit.id,
        logical_engine="gemini",
        transport_provider="google",
        transport_model="gemini-flash-latest",
    )
    session.add(engine_snapshot)
    await session.flush()
    analysis0 = await _add_analysis(
        session,
        workspace_id=workspace_id,
        audit=audit,
        snapshot=snapshots[0],
        engine_snapshot=engine_snapshot,
        prompt_index=0,
        owned_citation=False,
        competitor="Globex",
    )
    analysis1 = await _add_analysis(
        session,
        workspace_id=workspace_id,
        audit=audit,
        snapshot=snapshots[1],
        engine_snapshot=engine_snapshot,
        prompt_index=1,
        owned_citation=True,
        competitor=None,
    )
    metric_snapshot = None
    if with_metric_snapshot:
        metric_snapshot = MetricSnapshot(
            workspace_id=workspace_id,
            audit_id=audit.id,
            project_id=project_id,
            analyzer_version="b6-analysis-1",
            scoring_rule_version="scoring-v1",
            total_completed=2,
            visibility_score=50.0,
            metrics={},
            source_analysis_ids=[str(analysis0.id), str(analysis1.id)],
        )
        session.add(metric_snapshot)
        await session.flush()
    return audit, analysis0, analysis1, metric_snapshot


async def _add_issue(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    crawl: SiteCrawl,
    site_url: SiteUrl,
    rule_id: str,
    severity: str,
) -> SiteIssue:
    """Seed the full task/artifact/analysis/evaluation plumbing + issue."""
    task = SiteCrawlTask(
        crawl_id=crawl.id,
        workspace_id=workspace_id,
        task_kind=TASK_KIND_ANALYZE,
        requested_url=site_url.normalized_url,
        url_hash=site_url.url_hash,
        site_url_id=site_url.id,
        generation=INITIAL_TASK_GENERATION,
        idempotency_key=f"{crawl.id}:analyze:{site_url.id}:{rule_id}",
        status=TASK_STATUS_SUCCEEDED,
    )
    session.add(task)
    await session.flush()
    artifact = SiteFetchArtifact(
        task_id=task.id,
        crawl_id=crawl.id,
        workspace_id=workspace_id,
        fetch_purpose="analyze",
        requested_url=site_url.normalized_url,
        final_url=site_url.normalized_url,
        status_code=200,
        content_type="text/html",
        decoded_bytes=1024,
        normalized_facts={"has_html": True},
    )
    session.add(artifact)
    await session.flush()
    analysis = SitePageAnalysis(
        workspace_id=workspace_id,
        project_id=project_id,
        crawl_id=crawl.id,
        site_url_id=site_url.id,
        artifact_id=artifact.id,
        status=PAGE_ANALYSIS_STATUS_COMPLETED,
        analyzer_version="v1",
        scoring_version="v1",
    )
    session.add(analysis)
    await session.flush()
    evaluation = SiteRuleEvaluation(
        workspace_id=workspace_id,
        analysis_id=analysis.id,
        source_artifact_id=artifact.id,
        rule_id=rule_id,
        dimension="aeo",
        category="content",
        severity=severity,
        weight=1.0,
        outcome=RULE_OUTCOME_FAIL,
        evidence={"observed": "missing"},
        analyzer_version="v1",
        rule_version="v1",
    )
    session.add(evaluation)
    await session.flush()
    issue = SiteIssue(
        workspace_id=workspace_id,
        project_id=project_id,
        crawl_id=crawl.id,
        site_url_id=site_url.id,
        analysis_id=analysis.id,
        evaluation_id=evaluation.id,
        source_artifact_id=artifact.id,
        rule_id=rule_id,
        dimension="aeo",
        category="content",
        severity=severity,
        evidence={"observed": "missing"},
        remediation="Fix it.",
        analyzer_version="v1",
        rule_version="v1",
    )
    session.add(issue)
    await session.flush()
    return issue


async def _add_site(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> tuple[SiteCrawl, SiteIssue, SiteIssue]:
    profile = SiteHealthProfile(
        workspace_id=workspace_id,
        project_id=project_id,
        root_url=ROOT_URL,
        root_host="acme.test",
        registrable_domain="acme.test",
    )
    session.add(profile)
    await session.flush()
    crawl = SiteCrawl(
        workspace_id=workspace_id,
        project_id=project_id,
        profile_id=profile.id,
        status=CRAWL_STATUS_COMPLETED,
        root_url=ROOT_URL,
        random_seed="1",
        completed_at=datetime.now(UTC),
    )
    session.add(crawl)
    await session.flush()
    url_a = SiteUrl(
        workspace_id=workspace_id,
        project_id=project_id,
        normalized_url=URL_A,
        url_hash=_url_hash(URL_A),
    )
    url_b = SiteUrl(
        workspace_id=workspace_id,
        project_id=project_id,
        normalized_url=URL_B,
        url_hash=_url_hash(URL_B),
    )
    url_c = SiteUrl(
        workspace_id=workspace_id,
        project_id=project_id,
        normalized_url=URL_C,
        url_hash=_url_hash(URL_C),
    )
    session.add_all([url_a, url_b, url_c])
    await session.flush()
    issue_structured = await _add_issue(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        crawl=crawl,
        site_url=url_a,
        rule_id="aeo.structured_data_present",
        severity="medium",
    )
    issue_thin = await _add_issue(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        crawl=crawl,
        site_url=url_b,
        rule_id="aeo.sufficient_text",
        severity="low",
    )
    # Unmapped rule -> never becomes an opportunity (own URL: one analyze
    # task per (crawl, url) slot).
    await _add_issue(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        crawl=crawl,
        site_url=url_c,
        rule_id="technical.title_missing",
        severity="high",
    )
    return crawl, issue_structured, issue_thin


async def _seed_scenario(session: AsyncSession) -> Scenario:
    workspace_id, project_id, prompt_ids = await _seed_base(session)
    audit, analysis0, analysis1, metric_snapshot = await _add_visibility(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        prompt_ids=prompt_ids,
    )
    crawl, issue_structured, issue_thin = await _add_site(
        session, workspace_id=workspace_id, project_id=project_id
    )
    await session.commit()
    assert metric_snapshot is not None
    return Scenario(
        workspace_id=workspace_id,
        project_id=project_id,
        prompt0_id=prompt_ids[0],
        prompt1_id=prompt_ids[1],
        audit_id=audit.id,
        analysis0_id=analysis0.id,
        analysis1_id=analysis1.id,
        metric_snapshot_id=metric_snapshot.id,
        crawl_id=crawl.id,
        issue_structured_id=issue_structured.id,
        issue_thin_id=issue_thin.id,
    )


async def _live_rows(session: AsyncSession, scn: Scenario) -> list[Opportunity]:
    return list(
        (
            await session.scalars(
                select(Opportunity).where(
                    Opportunity.project_id == scn.project_id,
                    Opportunity.superseded_at.is_(None),
                )
            )
        ).all()
    )


def _by_rule(rows: list[Opportunity], rule_id: str) -> Opportunity:
    matches = [row for row in rows if row.rule_id == rule_id]
    assert len(matches) == 1, f"expected exactly one {rule_id} row, got {len(matches)}"
    return matches[0]


# =========================================================================
# Recompute: write path
# =========================================================================
async def test_recompute_persists_rows_and_snapshot_with_provenance(
    db_session: AsyncSession,
) -> None:
    scn = await _seed_scenario(db_session)

    result = await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )

    assert result["total_count"] == 4
    assert result["run_id"] is not None
    assert result["audit_id"] == scn.audit_id
    assert result["site_crawl_id"] == scn.crawl_id
    assert result["counts_by_type"] == {
        "site": 2,
        "topic": 0,
        "traffic": 0,
        "visibility": 2,
    }
    assert result["counts_by_severity"] == {
        "critical": 0,
        "high": 1,
        "info": 0,
        "low": 1,
        "medium": 2,
    }
    assert result["counts_by_status"] == {
        "dismissed": 0,
        "in_progress": 0,
        "open": 4,
        "resolved": 0,
    }
    # Median of [10.0, 20.0, 80.0, 120.0].
    assert result["median_priority"] == 50.0
    assert result["analyzer_version"] == ANALYZER_VERSION
    assert result["rule_version"] == RULE_VERSION
    assert result["formula_version"] == FORMULA_VERSION
    assert result["created_at"] is not None

    rows = await _live_rows(db_session, scn)
    assert len(rows) == 4

    brand_absent = _by_rule(rows, "brand_absent_high_value_prompt")
    assert brand_absent.target_key == f"prompt:{scn.prompt0_id}"
    assert brand_absent.target_prompt_id == scn.prompt0_id
    assert brand_absent.target_theme == "crm"
    assert brand_absent.opportunity_type == "visibility"
    assert brand_absent.severity == "high"
    assert brand_absent.priority_score == SCORE_BRAND_ABSENT
    assert brand_absent.status == "open"
    assert brand_absent.evidence is not None
    assert brand_absent.evidence["competitor_names"] == ["Globex"]
    assert brand_absent.evidence["prompt_intent"] == "purchase"
    assert brand_absent.evidence["prompt_text"] == "best crm for small teams"
    assert brand_absent.source_analysis_ids == [str(scn.analysis0_id)]
    assert brand_absent.source_metric_ids == [str(scn.metric_snapshot_id)]
    assert brand_absent.source_issue_ids == []
    assert brand_absent.source_traffic_ids is None
    assert brand_absent.analyzer_version == ANALYZER_VERSION
    assert brand_absent.rule_version == RULE_VERSION
    assert brand_absent.formula_version == FORMULA_VERSION

    owned_page = _by_rule(rows, "owned_page_not_cited")
    assert owned_page.target_key == f"prompt:{scn.prompt0_id}"
    assert owned_page.severity == "medium"
    assert owned_page.priority_score == SCORE_OWNED_PAGE
    assert owned_page.evidence is not None
    assert owned_page.evidence["owned_domains"] == ["acme.com"]

    structured = _by_rule(rows, "missing_structured_data")
    assert structured.target_key == f"url:{URL_A}"
    assert structured.target_url == URL_A
    assert structured.opportunity_type == "site"
    assert structured.priority_score == SCORE_STRUCTURED_DATA
    assert structured.source_issue_ids == [str(scn.issue_structured_id)]
    assert structured.evidence is not None
    assert structured.evidence["issue_rule_id"] == "aeo.structured_data_present"

    thin = _by_rule(rows, "thin_content")
    assert thin.target_key == f"url:{URL_B}"
    assert thin.priority_score == SCORE_THIN_CONTENT
    assert thin.source_issue_ids == [str(scn.issue_thin_id)]

    # The unmapped issue produced no row, and the owned-cited prompt none either.
    assert all(row.rule_id != "technical.title_missing" for row in rows)
    assert all(row.target_key != f"prompt:{scn.prompt1_id}" for row in rows)

    # The immutable snapshot persisted its sorted source-id aggregates.
    snapshot = await db_session.scalar(
        select(OpportunitySnapshot).where(
            OpportunitySnapshot.project_id == scn.project_id
        )
    )
    assert snapshot is not None
    assert snapshot.source_analysis_ids == sorted(
        [str(scn.analysis0_id)]
    )
    assert snapshot.source_issue_ids == sorted(
        [str(scn.issue_structured_id), str(scn.issue_thin_id)]
    )


async def test_recompute_without_sources_yields_empty_snapshot(
    db_session: AsyncSession,
) -> None:
    workspace_id, project_id, _prompt_ids = await _seed_base(db_session)
    await db_session.commit()

    result = await service.recompute(
        db_session, workspace_id=workspace_id, project_id=project_id
    )

    assert result["total_count"] == 0
    assert result["audit_id"] is None
    assert result["site_crawl_id"] is None
    assert result["median_priority"] is None
    assert result["counts_by_status"] == {
        "dismissed": 0,
        "in_progress": 0,
        "open": 0,
        "resolved": 0,
    }
    assert (
        await db_session.scalar(
            select(OpportunitySnapshot).where(
                OpportunitySnapshot.project_id == project_id
            )
        )
        is not None
    )


async def test_audit_without_metric_snapshot_is_not_dashboard_ready(
    db_session: AsyncSession,
) -> None:
    workspace_id, project_id, prompt_ids = await _seed_base(db_session)
    await _add_visibility(
        db_session,
        workspace_id=workspace_id,
        project_id=project_id,
        prompt_ids=prompt_ids,
        with_metric_snapshot=False,
    )
    await db_session.commit()

    result = await service.recompute(
        db_session, workspace_id=workspace_id, project_id=project_id
    )

    # Default resolution requires the aggregate snapshot (mirrors the
    # dashboard): the audit is treated as not ready, not as an error.
    assert result["audit_id"] is None
    assert result["total_count"] == 0


async def test_default_resolution_uses_latest_dashboard_ready_audit(
    db_session: AsyncSession,
) -> None:
    workspace_id, project_id, prompt_ids = await _seed_base(db_session)
    await _add_visibility(
        db_session,
        workspace_id=workspace_id,
        project_id=project_id,
        prompt_ids=prompt_ids,
    )
    await db_session.commit()
    # A newer completed audit with no analyses (but with its snapshot).
    newer = Audit(
        workspace_id=workspace_id,
        project_id=project_id,
        status=AUDIT_STATUS_COMPLETED,
        completed_at=datetime.now(UTC),
    )
    db_session.add(newer)
    await db_session.flush()
    db_session.add(
        MetricSnapshot(
            workspace_id=workspace_id,
            audit_id=newer.id,
            project_id=project_id,
            analyzer_version="b6-analysis-1",
            scoring_rule_version="scoring-v1",
            metrics={},
        )
    )
    await db_session.commit()

    result = await service.recompute(
        db_session, workspace_id=workspace_id, project_id=project_id
    )

    assert result["audit_id"] == newer.id
    assert result["counts_by_type"]["visibility"] == 0


async def test_explicit_foreign_audit_is_not_found(db_session: AsyncSession) -> None:
    scn = await _seed_scenario(db_session)
    foreign_workspace = Workspace(name="Foreign")
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_project = Project(
        workspace_id=foreign_workspace.id,
        name="Foreign",
        brand_name="F",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="consumer_like",
        default_repetitions=1,
    )
    db_session.add(foreign_project)
    await db_session.flush()
    foreign_audit = Audit(
        workspace_id=foreign_workspace.id,
        project_id=foreign_project.id,
        status=AUDIT_STATUS_COMPLETED,
    )
    db_session.add(foreign_audit)
    await db_session.commit()

    with pytest.raises(OpportunityNotFoundError):
        await service.recompute(
            db_session,
            workspace_id=scn.workspace_id,
            project_id=scn.project_id,
            audit_id=foreign_audit.id,
        )


async def test_missing_project_is_not_found(db_session: AsyncSession) -> None:
    with pytest.raises(OpportunityNotFoundError):
        await service.recompute(
            db_session, workspace_id=uuid.uuid4(), project_id=uuid.uuid4()
        )
    with pytest.raises(OpportunityNotFoundError):
        await service.list_opportunities(
            db_session, workspace_id=uuid.uuid4(), project_id=uuid.uuid4()
        )


async def test_disabled_rule_persists_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    scn = await _seed_scenario(db_session)
    monkeypatch.setattr(
        OPPORTUNITY_RULES_BY_ID["thin_content"], "enabled", False
    )

    result = await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )

    assert result["total_count"] == 3
    rows = await _live_rows(db_session, scn)
    assert all(row.rule_id != "thin_content" for row in rows)


# =========================================================================
# Supersede-not-mutate across recomputes
# =========================================================================
async def test_rerecompute_supersedes_carries_status_and_closes_vanished(
    db_session: AsyncSession,
) -> None:
    scn = await _seed_scenario(db_session)
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    first_rows = await _live_rows(db_session, scn)
    first_brand = _by_rule(first_rows, "brand_absent_high_value_prompt")
    first_thin = _by_rule(first_rows, "thin_content")
    first_structured = _by_rule(first_rows, "missing_structured_data")
    first_structured_evidence = dict(first_structured.evidence or {})

    # Human workflow state set between runs must survive the supersede.
    await service.update_status(
        db_session,
        workspace_id=scn.workspace_id,
        opportunity_id=first_brand.id,
        status="in_progress",
    )
    await service.update_status(
        db_session,
        workspace_id=scn.workspace_id,
        opportunity_id=first_thin.id,
        status="dismissed",
    )

    # The prompt-0 analysis gains an owned citation -> both visibility hits
    # vanish on the next pass.
    db_session.add(
        Citation(
            workspace_id=scn.workspace_id,
            audit_id=scn.audit_id,
            analysis_id=scn.analysis0_id,
            analyzer_version="b6-analysis-1",
            ordinal=2,
            url="https://acme.com/crm",
            title="Acme CRM",
            domain="acme.com",
            classification="owned",
            is_owned=True,
        )
    )
    await db_session.commit()

    result = await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )

    assert result["total_count"] == 2
    assert result["counts_by_status"]["open"] == 1
    assert result["counts_by_status"]["dismissed"] == 1

    live = await _live_rows(db_session, scn)
    assert {row.rule_id for row in live} == {
        "missing_structured_data",
        "thin_content",
    }
    new_thin = _by_rule(live, "thin_content")
    new_structured = _by_rule(live, "missing_structured_data")
    # New identities, carried status, byte-identical evidence.
    assert new_thin.id != first_thin.id
    assert new_thin.status == "dismissed"
    assert new_structured.id != first_structured.id
    assert new_structured.status == "open"
    assert new_structured.evidence == first_structured_evidence

    # Prior rows closed, never mutated.
    await db_session.refresh(first_brand)
    await db_session.refresh(first_thin)
    await db_session.refresh(first_structured)
    assert first_brand.superseded_at is not None
    assert first_brand.superseded_by_id is None  # vanished hit: no successor
    assert first_brand.status == "in_progress"  # untouched by the close
    assert first_thin.superseded_by_id == new_thin.id
    assert first_structured.superseded_by_id == new_structured.id


# =========================================================================
# Status mutation (the ONLY mutable field)
# =========================================================================
async def test_update_status_validates_persists_and_rejects_superseded(
    db_session: AsyncSession,
) -> None:
    scn = await _seed_scenario(db_session)
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    rows = await _live_rows(db_session, scn)
    thin = _by_rule(rows, "thin_content")
    evidence_before = dict(thin.evidence or {})

    item = await service.update_status(
        db_session,
        workspace_id=scn.workspace_id,
        opportunity_id=thin.id,
        status="resolved",
    )
    assert item["status"] == "resolved"
    await db_session.refresh(thin)
    assert thin.status == "resolved"
    assert thin.evidence == evidence_before  # mutation touched status only

    with pytest.raises(OpportunityValidationError):
        await service.update_status(
            db_session,
            workspace_id=scn.workspace_id,
            opportunity_id=thin.id,
            status="bogus",
        )
    with pytest.raises(OpportunityNotFoundError):
        await service.update_status(
            db_session,
            workspace_id=scn.workspace_id,
            opportunity_id=uuid.uuid4(),
            status="resolved",
        )

    # Supersede the row, then a mutation is a coded conflict.
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    await db_session.refresh(thin)
    assert thin.superseded_at is not None
    with pytest.raises(OpportunitySupersededError) as excinfo:
        await service.update_status(
            db_session,
            workspace_id=scn.workspace_id,
            opportunity_id=thin.id,
            status="open",
        )
    assert excinfo.value.code == CODE_OPPORTUNITY_SUPERSEDED


# =========================================================================
# Read projections: list / detail / summary / export rows
# =========================================================================
async def test_list_ordering_filters_and_keyset_pagination(
    db_session: AsyncSession,
) -> None:
    scn = await _seed_scenario(db_session)
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )

    page = await service.list_opportunities(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    assert [item["rule_id"] for item in page["items"]] == [
        "brand_absent_high_value_prompt",
        "owned_page_not_cited",
        "missing_structured_data",
        "thin_content",
    ]
    assert [item["priority_score"] for item in page["items"]] == [
        SCORE_BRAND_ABSENT,
        SCORE_OWNED_PAGE,
        SCORE_STRUCTURED_DATA,
        SCORE_THIN_CONTENT,
    ]
    assert page["next_cursor"] is None

    page1 = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        limit=2,
    )
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None
    page2 = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        limit=2,
        cursor=page1["next_cursor"],
    )
    assert [item["rule_id"] for item in page2["items"]] == [
        "missing_structured_data",
        "thin_content",
    ]
    assert page2["next_cursor"] is None
    assert {item["id"] for item in page1["items"]}.isdisjoint(
        {item["id"] for item in page2["items"]}
    )

    # A cursor is bound to its filter scope.
    with pytest.raises(InvalidCursorError):
        await service.list_opportunities(
            db_session,
            workspace_id=scn.workspace_id,
            project_id=scn.project_id,
            limit=2,
            cursor=page1["next_cursor"],
            severity="high",
        )
    with pytest.raises(InvalidCursorError):
        await service.list_opportunities(
            db_session,
            workspace_id=scn.workspace_id,
            project_id=scn.project_id,
            cursor="not-a-real-cursor",
        )

    by_type = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        opportunity_type="site",
    )
    assert {item["rule_id"] for item in by_type["items"]} == {
        "missing_structured_data",
        "thin_content",
    }
    by_severity = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        severity="high",
    )
    assert [item["rule_id"] for item in by_severity["items"]] == [
        "brand_absent_high_value_prompt"
    ]
    by_floor = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        min_priority=50.0,
    )
    assert [item["priority_score"] for item in by_floor["items"]] == [
        SCORE_BRAND_ABSENT,
        SCORE_OWNED_PAGE,
    ]
    by_rule = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        rule_id="thin_content",
    )
    assert len(by_rule["items"]) == 1
    dismissed = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        status="dismissed",
    )
    assert dismissed["items"] == []

    # Unknown tokens are validation errors.
    for kwargs in (
        {"opportunity_type": "bogus"},
        {"severity": "bogus"},
        {"status": "bogus"},
        {"rule_id": "bogus"},
    ):
        with pytest.raises(OpportunityValidationError):
            await service.list_opportunities(
                db_session,
                workspace_id=scn.workspace_id,
                project_id=scn.project_id,
                **kwargs,
            )


async def test_list_defaults_to_active_statuses(db_session: AsyncSession) -> None:
    scn = await _seed_scenario(db_session)
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    rows = await _live_rows(db_session, scn)
    thin = _by_rule(rows, "thin_content")
    await service.update_status(
        db_session,
        workspace_id=scn.workspace_id,
        opportunity_id=thin.id,
        status="dismissed",
    )

    default_page = await service.list_opportunities(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    assert len(default_page["items"]) == 3
    assert all(item["rule_id"] != "thin_content" for item in default_page["items"])
    dismissed_page = await service.list_opportunities(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        status="dismissed",
    )
    assert [item["rule_id"] for item in dismissed_page["items"]] == ["thin_content"]


async def test_detail_projection_includes_superseded_rows(
    db_session: AsyncSession,
) -> None:
    scn = await _seed_scenario(db_session)
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    rows = await _live_rows(db_session, scn)
    thin = _by_rule(rows, "thin_content")

    detail = await service.get_opportunity(
        db_session, workspace_id=scn.workspace_id, opportunity_id=thin.id
    )
    assert detail["id"] == thin.id
    assert detail["remediation"]
    assert detail["evidence"]["issue_rule_id"] == "aeo.sufficient_text"
    assert detail["source_issue_ids"] == [str(scn.issue_thin_id)]
    assert detail["source_traffic_ids"] == []
    assert detail["analyzer_version"] == ANALYZER_VERSION
    assert detail["superseded_at"] is None

    # After a recompute the OLD row is still readable, marked superseded.
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    detail = await service.get_opportunity(
        db_session, workspace_id=scn.workspace_id, opportunity_id=thin.id
    )
    assert detail["superseded_at"] is not None
    assert detail["superseded_by_id"] is not None

    with pytest.raises(OpportunityNotFoundError):
        await service.get_opportunity(
            db_session, workspace_id=scn.workspace_id, opportunity_id=uuid.uuid4()
        )


async def test_summary_before_and_after_recompute(db_session: AsyncSession) -> None:
    scn = await _seed_scenario(db_session)

    before = await service.get_summary(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    assert before["computed"] is False
    assert before["counts_by_type"] == {}
    assert before["total_count"] == 0
    assert before["run_id"] is None
    assert before["analyzer_version"] == ANALYZER_VERSION
    assert before["rule_version"] == RULE_VERSION
    assert before["formula_version"] == FORMULA_VERSION

    result = await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    after = await service.get_summary(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    assert after["computed"] is True
    assert after["run_id"] == result["run_id"]
    assert after["total_count"] == 4
    assert after["median_priority"] == 50.0
    assert after["counts_by_type"]["visibility"] == 2
    assert after["computed_at"] is not None


async def test_export_rows_projection_and_filters(db_session: AsyncSession) -> None:
    scn = await _seed_scenario(db_session)
    await service.recompute(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )

    rows = await service.load_export_rows(
        db_session, workspace_id=scn.workspace_id, project_id=scn.project_id
    )
    assert len(rows) == 4
    by_rule = {row["rule_id"]: row for row in rows}
    # Target resolution: prompt text for visibility, URL for site.
    assert by_rule["brand_absent_high_value_prompt"]["target"] == (
        "best crm for small teams"
    )
    assert by_rule["missing_structured_data"]["target"] == URL_A
    structured = by_rule["missing_structured_data"]
    assert structured["priority_score"] == SCORE_STRUCTURED_DATA
    assert structured["rule_version"] == RULE_VERSION
    assert structured["formula_version"] == FORMULA_VERSION
    assert structured["id"]

    site_only = await service.load_export_rows(
        db_session,
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        opportunity_type="site",
    )
    assert {row["rule_id"] for row in site_only} == {
        "missing_structured_data",
        "thin_content",
    }
    with pytest.raises(OpportunityValidationError):
        await service.load_export_rows(
            db_session,
            workspace_id=scn.workspace_id,
            project_id=scn.project_id,
            severity="bogus",
        )
