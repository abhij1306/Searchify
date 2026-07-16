"""Shared seed helpers for the B5 audit-execution component tests.

Builds a workspace + project + brand identity + a prompt set with N prompts +
one approved provider route/connection per requested engine, directly through
the ORM (no HTTP), so the planner + worker + queue can be exercised against a
real Postgres schema.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.provider_catalog import (
    ENGINE_GEMINI,
    TRANSPORT_GOOGLE,
    default_model,
)
from app.core.security import encrypt_secret
from app.models.brand import Brand, BrandAlias, Competitor, OwnedDomain
from app.models.project import Project
from app.models.prompt import Prompt, PromptSet
from app.models.provider import ProviderConnection, ProviderRoute
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


@dataclass
class Seed:
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    prompt_set_id: uuid.UUID
    prompt_ids: list[uuid.UUID]
    engines: list[str]


async def seed_audit_fixtures(
    session: AsyncSession,
    *,
    prompt_count: int = 3,
    engines: list[str] | None = None,
    email: str | None = None,
) -> Seed:
    engines = engines or [ENGINE_GEMINI]
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"

    workspace = Workspace(name="Test WS")
    session.add(workspace)
    await session.flush()

    user = User(email=email, hashed_password="x", is_active=True)
    session.add(user)
    await session.flush()

    session.add(
        WorkspaceMember(
            workspace_id=workspace.id, user_id=user.id, role="owner"
        )
    )

    project = Project(
        workspace_id=workspace.id,
        name="Acme Visibility",
        brand_name="Acme Corp",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="consumer_like",
        default_repetitions=3,
    )
    session.add(project)
    await session.flush()

    brand = Brand(project_id=project.id, name="Acme Corp")
    session.add(brand)
    await session.flush()
    session.add(BrandAlias(brand_id=brand.id, alias="Acme"))
    session.add(
        Competitor(
            project_id=project.id,
            name="Globex",
            aliases=["Globex Co"],
            domains=["globex.com"],
        )
    )
    session.add(OwnedDomain(project_id=project.id, domain="acme.com"))

    prompt_set = PromptSet(project_id=project.id, name="Default")
    session.add(prompt_set)
    await session.flush()

    prompt_ids: list[uuid.UUID] = []
    for index in range(prompt_count):
        prompt = Prompt(
            prompt_set_id=prompt_set.id,
            text=f"best option {index}",
            theme="general",
            intent="category",
            enabled=True,
            origin="manual",
        )
        session.add(prompt)
        await session.flush()
        prompt_ids.append(prompt.id)

    # One approved connection + default route per requested engine. Gemini via
    # google is the simplest approved route; others resolve their catalog model.
    for engine in engines:
        transport = (
            TRANSPORT_GOOGLE
            if engine == ENGINE_GEMINI
            else _transport_for(engine)
        )
        connection = ProviderConnection(
            workspace_id=workspace.id,
            label=f"{engine} key",
            transport_provider=transport,
            api_key_encrypted=encrypt_secret("secret-test-key"),
            active=True,
        )
        session.add(connection)
        await session.flush()
        session.add(
            ProviderRoute(
                workspace_id=workspace.id,
                connection_id=connection.id,
                logical_engine=engine,
                transport_provider=transport,
                transport_model=default_model(engine, transport),
                is_default=True,
            )
        )

    await session.commit()
    return Seed(
        workspace_id=workspace.id,
        project_id=project.id,
        prompt_set_id=prompt_set.id,
        prompt_ids=prompt_ids,
        engines=list(engines),
    )


def _transport_for(engine: str) -> str:
    from app.core.config.provider_catalog import (
        ENGINE_CHATGPT,
        ENGINE_CLAUDE,
        TRANSPORT_ANTHROPIC,
        TRANSPORT_OPENROUTER,
    )

    if engine == ENGINE_CLAUDE:
        return TRANSPORT_ANTHROPIC
    if engine == ENGINE_CHATGPT:
        return TRANSPORT_OPENROUTER
    return TRANSPORT_OPENROUTER
