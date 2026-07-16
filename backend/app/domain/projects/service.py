# Project + normalized brand-identity service (workspace-scoped, invariant 5).
#
# Every read/write filters by ``workspace_id`` (never ``user_id``). The service
# owns translating the flat create/update payload into the normalized brand
# rows (B-1) and back, and reuses ``normalization.py`` for benchmark-mode
# canonicalization.
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.projects.normalization import normalize_benchmark_mode
from app.domain.projects.schemas import (
    BrandResponse,
    CompetitorResponse,
    ProjectResponse,
)
from app.domain.prompts.mappers import prompt_set_to_response
from app.models.brand import (
    Brand,
    BrandAlias,
    Competitor,
    OwnedDomain,
    UnintendedDomain,
)
from app.models.project import Project
from app.models.prompt import PromptSet


class ProjectNotFoundError(LookupError):
    """Raised when a project is missing or not in the caller's workspace."""


def _loaded_project_query():
    """A select over ``Project`` with all brand-identity rows eager-loaded."""
    return select(Project).options(
        selectinload(Project.brand).selectinload(Brand.aliases),
        selectinload(Project.competitors),
        selectinload(Project.owned_domains),
        selectinload(Project.unintended_domains),
        selectinload(Project.prompt_sets).selectinload(PromptSet.prompts),
    )


def _clean_list(values: list[str] | None) -> list[str]:
    return [str(v).strip() for v in (values or []) if str(v).strip()]


def project_to_response(project: Project) -> ProjectResponse:
    """Project the normalized rows back into the flat response DTO."""
    brand = project.brand
    return ProjectResponse(
        id=project.id,
        workspace_id=project.workspace_id,
        name=project.name,
        brand_name=(brand.name if brand is not None else project.brand_name),
        brand=BrandResponse(
            aliases=(
                [alias.alias for alias in brand.aliases]
                if brand is not None
                else []
            )
        ),
        website_url=project.website_url,
        owned_domains=[d.domain for d in project.owned_domains],
        unintended_domains=[d.domain for d in project.unintended_domains],
        competitors=[
            CompetitorResponse(
                id=c.id,
                name=c.name,
                aliases=list(c.aliases or []),
                domains=list(c.domains or []),
            )
            for c in project.competitors
        ],
        prompt_sets=[
            prompt_set_to_response(ps) for ps in project.prompt_sets
        ],
        country_code=project.country_code,
        language_code=project.language_code,
        benchmark_mode=project.benchmark_mode,
        default_repetitions=project.default_repetitions,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _apply_brand(project: Project, brand_name: str, aliases: list[str]) -> None:
    """(Re)build the brand + its aliases on a project in place.

    When a brand row already exists it is mutated in place (rather than
    replaced) so the ``uq_brand_project`` unique constraint is never briefly
    violated by an INSERT-before-DELETE ordering during an update.
    """
    brand = project.brand
    if brand is None:
        brand = Brand(name=brand_name)
        project.brand = brand
    else:
        brand.name = brand_name
    brand.aliases = [BrandAlias(alias=a) for a in _clean_list(aliases)]
    project.brand_name = brand_name


def _build_competitors(items: list[Any] | None) -> list[Competitor]:
    competitors: list[Competitor] = []
    for item in items or []:
        name = str(getattr(item, "name", "") or "").strip()
        if not name:
            continue
        competitors.append(
            Competitor(
                name=name,
                aliases=_clean_list(list(getattr(item, "aliases", []) or [])),
                domains=_clean_list(list(getattr(item, "domains", []) or [])),
            )
        )
    return competitors


async def create_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, payload: Any
) -> Project:
    """Create a project + its normalized brand identity in one transaction."""
    project = Project(
        workspace_id=workspace_id,
        name=payload.name,
        website_url=payload.website_url,
        country_code=payload.country_code,
        language_code=payload.language_code,
        benchmark_mode=normalize_benchmark_mode(payload.benchmark_mode),
        default_repetitions=payload.default_repetitions,
    )
    _apply_brand(project, payload.brand_name, payload.brand_aliases)
    project.competitors = _build_competitors(payload.competitors)
    project.owned_domains = [
        OwnedDomain(domain=d) for d in _clean_list(payload.owned_domains)
    ]
    project.unintended_domains = [
        UnintendedDomain(domain=d)
        for d in _clean_list(payload.unintended_domains)
    ]
    session.add(project)
    await session.commit()
    return await get_project(
        session, workspace_id=workspace_id, project_id=project.id
    )


async def list_projects(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[Project]:
    result = await session.execute(
        _loaded_project_query()
        .where(Project.workspace_id == workspace_id)
        .order_by(Project.created_at.desc())
    )
    return list(result.scalars().unique().all())


async def get_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    result = await session.execute(
        _loaded_project_query().where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
    )
    project = result.scalars().unique().one_or_none()
    if project is None:
        raise ProjectNotFoundError("Project not found")
    return project


async def update_project(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: Any,
) -> Project:
    project = await get_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"] is not None:
        project.name = data["name"]
    if "website_url" in data and data["website_url"] is not None:
        project.website_url = data["website_url"]
    if "country_code" in data and data["country_code"] is not None:
        project.country_code = data["country_code"]
    if "language_code" in data and data["language_code"] is not None:
        project.language_code = data["language_code"]
    if "benchmark_mode" in data and data["benchmark_mode"] is not None:
        project.benchmark_mode = normalize_benchmark_mode(data["benchmark_mode"])
    if (
        "default_repetitions" in data
        and data["default_repetitions"] is not None
    ):
        project.default_repetitions = data["default_repetitions"]

    # Brand name / aliases are rebuilt together so the alias set stays
    # consistent with the (possibly new) brand name.
    if ("brand_name" in data and data["brand_name"] is not None) or (
        payload.brand is not None
    ):
        brand = project.brand
        new_name = (
            data["brand_name"]
            if data.get("brand_name") is not None
            else (brand.name if brand is not None else project.brand_name)
        )
        new_aliases = (
            payload.brand.aliases
            if payload.brand is not None
            else ([a.alias for a in brand.aliases] if brand is not None else [])
        )
        _apply_brand(project, new_name, new_aliases)

    if "competitors" in data and data["competitors"] is not None:
        project.competitors = _build_competitors(payload.competitors)
    if "owned_domains" in data and data["owned_domains"] is not None:
        project.owned_domains = [
            OwnedDomain(domain=d) for d in _clean_list(data["owned_domains"])
        ]
    if "unintended_domains" in data and data["unintended_domains"] is not None:
        project.unintended_domains = [
            UnintendedDomain(domain=d)
            for d in _clean_list(data["unintended_domains"])
        ]

    await session.commit()
    return await get_project(
        session, workspace_id=workspace_id, project_id=project_id
    )


async def delete_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    project = await get_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    await session.delete(project)
    await session.commit()
