# Products router: catalog CRUD + CSV import + competitor products.
#
# Workspace-scoped through the parent project (invariant 5); the active
# workspace is resolved by ``require_active_workspace`` (flat MVP surface —
# mirrors ``api/prompts.py``). The surface:
#   - GET/POST /projects/{project_id}/products
#   - GET/PATCH/DELETE /products/{product_id}
#   - POST /projects/{project_id}/products/import -> CSV/JSON bulk-create
#   - GET/POST /projects/{project_id}/competitor-products
#   - PATCH/DELETE /competitor-products/{competitor_product_id}
#   - (Task 4) product visibility projections + CSV export
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.core.config.products import (
    PRODUCT_EVIDENCE_DEFAULT_LIMIT,
    PRODUCT_EVIDENCE_MAX_LIMIT,
)
from app.core.http_errors import raise_not_found
from app.domain.analysis.service import AnalysisNotFoundError, TrendQueryError
from app.domain.products.csv_import import ProductCsvError, parse_product_csv
from app.domain.products.schemas import (
    CompetitorProductInput,
    CompetitorProductResponse,
    CompetitorProductUpdate,
    ProductEvidenceResponse,
    ProductImport,
    ProductInput,
    ProductResponse,
    ProductUpdate,
    ProductVisibilityResponse,
    competitor_product_to_response,
    product_to_response,
)
from app.domain.products.service import (
    CompetitorNotFoundError,
    CompetitorProductNotFoundError,
    DuplicateProductError,
    ProductImportError,
    ProductNotFoundError,
    create_competitor_product,
    create_product,
    delete_competitor_product,
    delete_product,
    get_product,
    import_products,
    list_competitor_products,
    list_products,
    update_competitor_product,
    update_product,
)
from app.domain.products.visibility import (
    get_product_evidence,
    get_product_visibility,
    load_product_visibility_export_bundle,
    product_visibility_csv,
)

router = APIRouter(tags=["products"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]

_RES_PROJECT = "Project"
_RES_PRODUCT = "Product"
_RES_COMPETITOR_PRODUCT = "Competitor product"


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
    )


# --------------------------------------------------------------------------
# Own catalog
# --------------------------------------------------------------------------
@router.get("/projects/{project_id}/products", response_model=list[ProductResponse])
async def list_products_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> list[ProductResponse]:
    try:
        products = await list_products(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PROJECT, cause=exc)
    return [product_to_response(p) for p in products]


@router.post(
    "/projects/{project_id}/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_product_endpoint(
    project_id: uuid.UUID,
    payload: ProductInput,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> ProductResponse:
    try:
        product = await create_product(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            payload=payload,
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PROJECT, cause=exc)
    except DuplicateProductError as exc:
        raise _conflict(str(exc)) from exc
    return product_to_response(product)


@router.get("/products/{product_id}", response_model=ProductResponse)
async def get_product_endpoint(
    product_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ProductResponse:
    try:
        product = await get_product(
            session, workspace_id=ctx.workspace_id, product_id=product_id
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PRODUCT, cause=exc)
    return product_to_response(product)


@router.patch("/products/{product_id}", response_model=ProductResponse)
async def update_product_endpoint(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> ProductResponse:
    try:
        product = await update_product(
            session,
            workspace_id=ctx.workspace_id,
            product_id=product_id,
            payload=payload,
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PRODUCT, cause=exc)
    except DuplicateProductError as exc:
        raise _conflict(str(exc)) from exc
    return product_to_response(product)


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_endpoint(
    product_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_product(
            session, workspace_id=ctx.workspace_id, product_id=product_id
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PRODUCT, cause=exc)


# --------------------------------------------------------------------------
# CSV / JSON-rows bulk import (mirrors the prompts import flow)
# --------------------------------------------------------------------------
async def _resolve_import_rows(
    request: Request, file: UploadFile | None
) -> list[ProductInput]:
    """Accept either a multipart CSV upload or a JSON body of parsed rows.

    Both converge to a list of ``ProductInput`` for the service (mirrors
    ``api/prompts.py``). Malformed CSV (e.g. headerless) is a 422, not a 500.
    """
    if file is not None:
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        return parse_product_csv(raw)

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        return ProductImport.model_validate(body).products

    # Raw CSV posted as text/csv (no multipart wrapper).
    raw_body = (await request.body()).decode("utf-8-sig", errors="replace")
    return parse_product_csv(raw_body)


@router.post(
    "/projects/{project_id}/products/import",
    response_model=list[ProductResponse],
    status_code=status.HTTP_201_CREATED,
)
async def import_products_endpoint(
    project_id: uuid.UUID,
    request: Request,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    file: UploadFile | None = None,
) -> list[ProductResponse]:
    try:
        rows = await _resolve_import_rows(request, file)
    except ProductCsvError as exc:
        raise _unprocessable(str(exc)) from exc
    try:
        products = await import_products(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            rows=rows,
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PROJECT, cause=exc)
    except ProductImportError as exc:
        raise _unprocessable(str(exc)) from exc
    return [product_to_response(p) for p in products]


# --------------------------------------------------------------------------
# Competitor products
# --------------------------------------------------------------------------
@router.get(
    "/projects/{project_id}/competitor-products",
    response_model=list[CompetitorProductResponse],
)
async def list_competitor_products_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> list[CompetitorProductResponse]:
    try:
        competitor_products = await list_competitor_products(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PROJECT, cause=exc)
    return [competitor_product_to_response(cp) for cp in competitor_products]


@router.post(
    "/projects/{project_id}/competitor-products",
    response_model=CompetitorProductResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_competitor_product_endpoint(
    project_id: uuid.UUID,
    payload: CompetitorProductInput,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> CompetitorProductResponse:
    try:
        competitor_product = await create_competitor_product(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            payload=payload,
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PROJECT, cause=exc)
    except CompetitorNotFoundError as exc:
        # The FK'd competitor is missing/cross-project/cross-workspace: 404,
        # never an existence oracle (invariant 5).
        raise _not_found(str(exc)) from exc
    except DuplicateProductError as exc:
        raise _conflict(str(exc)) from exc
    return competitor_product_to_response(competitor_product)


@router.patch(
    "/competitor-products/{competitor_product_id}",
    response_model=CompetitorProductResponse,
)
async def update_competitor_product_endpoint(
    competitor_product_id: uuid.UUID,
    payload: CompetitorProductUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> CompetitorProductResponse:
    try:
        competitor_product = await update_competitor_product(
            session,
            workspace_id=ctx.workspace_id,
            competitor_product_id=competitor_product_id,
            payload=payload,
        )
    except CompetitorProductNotFoundError as exc:
        raise_not_found(_RES_COMPETITOR_PRODUCT, cause=exc)
    except DuplicateProductError as exc:
        raise _conflict(str(exc)) from exc
    return competitor_product_to_response(competitor_product)


@router.delete(
    "/competitor-products/{competitor_product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_competitor_product_endpoint(
    competitor_product_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_competitor_product(
            session,
            workspace_id=ctx.workspace_id,
            competitor_product_id=competitor_product_id,
        )
    except CompetitorProductNotFoundError as exc:
        raise_not_found(_RES_COMPETITOR_PRODUCT, cause=exc)


# --------------------------------------------------------------------------
# Visibility projections (persisted rows only, invariant 7)
# --------------------------------------------------------------------------
@router.get(
    "/projects/{project_id}/products/visibility",
    response_model=ProductVisibilityResponse,
)
async def product_visibility_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    audit_id: Annotated[uuid.UUID | None, Query()] = None,
    engine: Annotated[str | None, Query()] = None,
) -> ProductVisibilityResponse:
    """Selected-audit product dashboard (defaults to the latest product audit)."""
    try:
        return await get_product_visibility(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            audit_id=audit_id,
            engine=engine,
        )
    except AnalysisNotFoundError as exc:
        raise_not_found("Product visibility", cause=exc)
    except TrendQueryError as exc:
        raise _unprocessable(str(exc)) from exc


@router.get(
    "/products/{product_id}/visibility/evidence",
    response_model=ProductEvidenceResponse,
)
async def product_evidence_endpoint(
    product_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    audit_id: Annotated[uuid.UUID | None, Query()] = None,
    engine: Annotated[str | None, Query()] = None,
    limit: Annotated[
        int, Query(ge=1, le=PRODUCT_EVIDENCE_MAX_LIMIT)
    ] = PRODUCT_EVIDENCE_DEFAULT_LIMIT,
) -> ProductEvidenceResponse:
    """Persisted mention evidence for one product (bounded, newest-first)."""
    try:
        return await get_product_evidence(
            session,
            workspace_id=ctx.workspace_id,
            product_id=product_id,
            audit_id=audit_id,
            engine=engine,
            limit=limit,
        )
    except ProductNotFoundError as exc:
        raise_not_found(_RES_PRODUCT, cause=exc)
    except AnalysisNotFoundError as exc:
        raise_not_found("Audit", cause=exc)
    except TrendQueryError as exc:
        raise _unprocessable(str(exc)) from exc


@router.get("/projects/{project_id}/products/visibility/export.csv")
async def product_visibility_export_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    audit_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Response:
    """Download the per-entry product visibility rows as CSV (persisted rows)."""
    try:
        audit, snapshots = await load_product_visibility_export_bundle(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            audit_id=audit_id,
        )
    except AnalysisNotFoundError as exc:
        raise_not_found("Product visibility", cause=exc)
    body = product_visibility_csv(audit, snapshots)
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="product-visibility-{audit.id}.csv"'
            )
        },
    )
