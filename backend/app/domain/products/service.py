# Product-catalog service (workspace-scoped through the project, invariant 5).
#
# A product belongs to a project, which is workspace-scoped, so every query
# joins through ``Project`` and filters by ``workspace_id`` — mirroring
# ``domain/prompts/service.py``. Owns manual CRUD + CSV bulk import for both
# the own catalog (``Product``) and competitor products (``CompetitorProduct``).
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.products import (
    PRODUCT_IMPORT_MAX_ROWS,
    PRODUCT_ORIGIN_IMPORTED,
    PRODUCT_ORIGIN_MANUAL,
)
from app.models.brand import Competitor
from app.models.product import CompetitorProduct, Product
from app.models.project import Project


class ProductNotFoundError(LookupError):
    """Raised when a product (or its parent project) is missing or not in the
    caller's workspace."""


class CompetitorProductNotFoundError(LookupError):
    """Raised when a competitor product is missing or not in the caller's
    workspace."""


class CompetitorNotFoundError(LookupError):
    """Raised when the FK'd competitor is missing, cross-workspace, or not in
    the request's project."""


class DuplicateProductError(ValueError):
    """Raised when ``(project_id, sku)`` or ``(competitor_id, name)`` collides."""


class ProductImportError(ValueError):
    """Raised when an import payload violates the config caps."""


async def _project_in_workspace(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    result = await session.execute(
        select(Project).where(
            Project.id == project_id, Project.workspace_id == workspace_id
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise ProductNotFoundError("Project not found")
    return project


# --------------------------------------------------------------------------
# Own catalog (Product)
# --------------------------------------------------------------------------
async def list_products(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> list[Product]:
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    result = await session.execute(
        select(Product)
        .where(Product.project_id == project_id)
        .order_by(Product.created_at.asc())
    )
    return list(result.scalars().all())


async def get_product(
    session: AsyncSession, *, workspace_id: uuid.UUID, product_id: uuid.UUID
) -> Product:
    result = await session.execute(
        select(Product)
        .join(Project, Project.id == Product.project_id)
        .where(
            Product.id == product_id,
            Project.workspace_id == workspace_id,
        )
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise ProductNotFoundError("Product not found")
    return product


def _apply_product_fields(product: Product, data: dict[str, Any]) -> None:
    for field in ("sku", "name", "url", "currency"):
        # Non-nullable columns: apply only when a value is actually provided.
        if data.get(field) is not None:
            setattr(product, field, str(data[field]).strip())
    for field in ("aliases", "variants", "attributes"):
        if data.get(field) is not None:
            setattr(product, field, data[field])
    # ``price`` is the one nullable field: an explicit JSON null clears it.
    if "price" in data:
        product.price = data["price"]


async def create_product(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: Any,
) -> Product:
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    product = Product(project_id=project_id, origin=PRODUCT_ORIGIN_MANUAL)
    _apply_product_fields(product, payload.model_dump(exclude_unset=True))
    session.add(product)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProductError(
            "A product with this SKU already exists in this project"
        ) from exc
    await session.refresh(product)
    return product


async def update_product(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: Any,
) -> Product:
    product = await get_product(
        session, workspace_id=workspace_id, product_id=product_id
    )
    _apply_product_fields(product, payload.model_dump(exclude_unset=True))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProductError(
            "A product with this SKU already exists in this project"
        ) from exc
    await session.refresh(product)
    return product


async def delete_product(
    session: AsyncSession, *, workspace_id: uuid.UUID, product_id: uuid.UUID
) -> None:
    product = await get_product(
        session, workspace_id=workspace_id, product_id=product_id
    )
    await session.delete(product)
    await session.commit()


async def import_products(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    rows: list[Any],
) -> list[Product]:
    """CSV bulk-create: persist already-parsed product rows as ``imported``.

    Rows with an empty sku are skipped (the parser already drops them; the
    JSON path re-checks). Duplicates are dropped keeping the FIRST occurrence,
    never a request failure: a repeat within the upload is filtered before the
    insert, and a clash with an existing product is dropped by ``ON CONFLICT DO
    NOTHING`` on the per-project sku constraint. Returns the full refreshed
    catalog so the caller projects the whole table back.
    """
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if len(rows) > PRODUCT_IMPORT_MAX_ROWS:
        raise ProductImportError(
            f"Import accepts at most {PRODUCT_IMPORT_MAX_ROWS} rows"
        )
    # One multi-VALUES INSERT rather than a statement per row: the cap is 500
    # rows, so a per-row execute costs up to 500 round-trips per import.
    values = []
    seen_skus: set[str] = set()
    for row in rows:
        sku = str(row.sku or "").strip()
        # Within-batch duplicates must be dropped here: ON CONFLICT DO NOTHING
        # cannot resolve two conflicting rows in the SAME statement (Postgres
        # raises "cannot affect row a second time").
        if not sku or sku in seen_skus:
            continue
        seen_skus.add(sku)
        values.append(
            {
                "id": uuid.uuid4(),
                "project_id": project_id,
                "sku": sku,
                "name": str(row.name or "").strip() or sku,
                "aliases": list(row.aliases or []),
                "variants": [v.model_dump() for v in (row.variants or [])],
                "price": row.price,
                "currency": str(row.currency or "").strip().upper(),
                "url": str(row.url or "").strip(),
                "attributes": dict(row.attributes or {}),
                "origin": PRODUCT_ORIGIN_IMPORTED,
            }
        )
    if values:
        await session.execute(
            pg_insert(Product)
            .values(values)
            .on_conflict_do_nothing(constraint="uq_product_project_sku")
        )
    await session.commit()
    return await list_products(
        session, workspace_id=workspace_id, project_id=project_id
    )


# --------------------------------------------------------------------------
# Competitor products (CompetitorProduct)
# --------------------------------------------------------------------------
async def list_competitor_products(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> list[CompetitorProduct]:
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    result = await session.execute(
        select(CompetitorProduct)
        .where(CompetitorProduct.project_id == project_id)
        .order_by(CompetitorProduct.created_at.asc())
    )
    return list(result.scalars().all())


async def _competitor_in_project(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    competitor_id: uuid.UUID,
) -> Competitor:
    result = await session.execute(
        select(Competitor)
        .join(Project, Project.id == Competitor.project_id)
        .where(
            Competitor.id == competitor_id,
            Competitor.project_id == project_id,
            Project.workspace_id == workspace_id,
        )
    )
    competitor = result.scalar_one_or_none()
    if competitor is None:
        raise CompetitorNotFoundError("Competitor not found in this project")
    return competitor


async def _get_competitor_product(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    competitor_product_id: uuid.UUID,
) -> CompetitorProduct:
    result = await session.execute(
        select(CompetitorProduct)
        .join(Project, Project.id == CompetitorProduct.project_id)
        .where(
            CompetitorProduct.id == competitor_product_id,
            Project.workspace_id == workspace_id,
        )
    )
    competitor_product = result.scalar_one_or_none()
    if competitor_product is None:
        raise CompetitorProductNotFoundError("Competitor product not found")
    return competitor_product


def _apply_competitor_product_fields(
    competitor_product: CompetitorProduct, data: dict[str, Any]
) -> None:
    for field in ("name", "url", "currency"):
        if data.get(field) is not None:
            setattr(competitor_product, field, str(data[field]).strip())
    if data.get("aliases") is not None:
        competitor_product.aliases = data["aliases"]
    # ``price`` is the one nullable field: an explicit JSON null clears it.
    if "price" in data:
        competitor_product.price = data["price"]


async def create_competitor_product(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: Any,
) -> CompetitorProduct:
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    competitor = await _competitor_in_project(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        competitor_id=payload.competitor_id,
    )
    competitor_product = CompetitorProduct(
        project_id=project_id, competitor_id=competitor.id
    )
    _apply_competitor_product_fields(
        competitor_product, payload.model_dump(exclude_unset=True)
    )
    session.add(competitor_product)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProductError(
            "A product with this name already exists for this competitor"
        ) from exc
    await session.refresh(competitor_product)
    return competitor_product


async def update_competitor_product(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    competitor_product_id: uuid.UUID,
    payload: Any,
) -> CompetitorProduct:
    competitor_product = await _get_competitor_product(
        session, workspace_id=workspace_id, competitor_product_id=competitor_product_id
    )
    _apply_competitor_product_fields(
        competitor_product, payload.model_dump(exclude_unset=True)
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProductError(
            "A product with this name already exists for this competitor"
        ) from exc
    await session.refresh(competitor_product)
    return competitor_product


async def delete_competitor_product(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    competitor_product_id: uuid.UUID,
) -> None:
    competitor_product = await _get_competitor_product(
        session, workspace_id=workspace_id, competitor_product_id=competitor_product_id
    )
    await session.delete(competitor_product)
    await session.commit()
