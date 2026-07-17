"""Shared seed helpers for the Task 1 Site Health component tests.

Builds a workspace + project + Site Health profile + crawl, and enqueues
``SiteCrawlTask`` queue rows directly through the ORM (no HTTP), so the generic
``PostgresTaskQueue`` (parameterized by ``SITE_CRAWL_QUEUE_SPEC``) can be
exercised against a real Postgres schema exactly like the audit queue.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.web_evidence.url_policy import (
    canonicalize,
    registrable_domain,
    split_host_port,
)
from app.core.config.site_health import (
    CRAWL_STATUS_RUNNING,
    INITIAL_TASK_GENERATION,
    TASK_KIND_DISCOVER,
)
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.models.project import Project
from app.models.site_health import (
    SiteCrawl,
    SiteCrawlTask,
    SiteHealthProfile,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


@dataclass
class SiteSeed:
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    profile_id: uuid.UUID
    crawl_id: uuid.UUID
    task_ids: list[uuid.UUID] = field(default_factory=list)


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:64]


async def seed_site_crawl(
    session: AsyncSession,
    *,
    task_count: int = 0,
    email: str | None = None,
    root_url: str = "https://example.com/",
) -> SiteSeed:
    """Seed a workspace/project/profile/crawl and ``task_count`` queued tasks."""
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"

    # Derive the canonical root + registrable host through the production
    # URL-policy utilities (never hard-coded) so a custom-host root actually
    # produces a matching profile, and a root without a trailing slash still
    # joins task URLs safely (no "https://host page-0" malformed URL).
    canonical_root = canonicalize(root_url)
    root_host, _root_port = split_host_port(canonical_root)
    root_base = (
        canonical_root if canonical_root.endswith("/") else (canonical_root + "/")
    )

    workspace = Workspace(name="Site WS")
    session.add(workspace)
    await session.flush()

    user = User(email=email, hashed_password="x", is_active=True)
    session.add(user)
    await session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    )

    project = Project(
        workspace_id=workspace.id,
        name="Acme Site",
        brand_name="Acme Corp",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="consumer_like",
        default_repetitions=1,
        website_url=canonical_root,
    )
    session.add(project)
    await session.flush()

    profile = SiteHealthProfile(
        workspace_id=workspace.id,
        project_id=project.id,
        root_url=canonical_root,
        root_host=root_host,
        registrable_domain=registrable_domain(root_host),
    )
    session.add(profile)
    await session.flush()

    crawl = SiteCrawl(
        workspace_id=workspace.id,
        project_id=project.id,
        profile_id=profile.id,
        status=CRAWL_STATUS_RUNNING,
        root_url=canonical_root,
        random_seed="1",
    )
    session.add(crawl)
    await session.flush()

    tasks: list[SiteCrawlTask] = []
    for i in range(task_count):
        url = f"{root_base}page-{i}"
        task = SiteCrawlTask(
            crawl_id=crawl.id,
            workspace_id=workspace.id,
            task_kind=TASK_KIND_DISCOVER,
            requested_url=url,
            url_hash=_url_hash(url),
            generation=INITIAL_TASK_GENERATION,
            idempotency_key=f"{crawl.id}:{TASK_KIND_DISCOVER}:{i}:0",
            status=TASK_STATUS_QUEUED,
            randomized_position=i,
        )
        session.add(task)
        tasks.append(task)
    await session.flush()
    task_ids: list[uuid.UUID] = [task.id for task in tasks]
    await session.commit()

    return SiteSeed(
        workspace_id=workspace.id,
        project_id=project.id,
        profile_id=profile.id,
        crawl_id=crawl.id,
        task_ids=task_ids,
    )
