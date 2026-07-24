"""Shared seed helpers for the opportunities component tests (service + API).

Builds a workspace + project + owned domain + two prompts, one completed
audit with prompt snapshots / analyses / citations / a metric snapshot, and
one completed site crawl with two mapped issues (plus one unmapped), directly
through the ORM so the recompute service + router can be exercised against a
real Postgres schema.

Expected scores for the seeded scenario (severity * value * gap * 10):
- brand_absent_high_value_prompt: high 3.0 * purchase 2.0 * gap 2.0 = 120.0
- owned_page_not_cited:           medium 2.0 * purchase 2.0 * gap 2.0 = 80.0
- missing_structured_data:        medium 2.0 * 1.0 * 1.0 = 20.0
- thin_content:                   low 1.0 * 1.0 * 1.0 = 10.0 (floor edge)
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.audits import AUDIT_STATUS_COMPLETED
from app.core.config.site_health import (
    CRAWL_STATUS_COMPLETED,
    INITIAL_TASK_GENERATION,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    RULE_OUTCOME_FAIL,
    TASK_KIND_ANALYZE,
)
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
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
from app.models.opportunity import Opportunity
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
    session: AsyncSession, *, email: str | None = None
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    workspace = Workspace(name="Opp WS")
    session.add(workspace)
    await session.flush()
    if email is not None:
        # The user already exists (created via `/auth/register`); attach it.
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
    else:
        user = User(
            email=f"user-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
        )
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


async def _seed_scenario(
    session: AsyncSession, *, email: str | None = None
) -> Scenario:
    workspace_id, project_id, prompt_ids = await _seed_base(session, email=email)
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

