# Executions router: single-execution evidence (B6, invariants 5/7).
#
# ``GET /executions/{id}`` serves one execution's persisted ``ResponseAnalysis``
# + its classified citation evidence. It is a top-level route (the plan lists it
# as ``executions/{id}``); the ``{id}`` is the analysis id. Workspace-scoped and
# projection-only — it reads persisted rows and never calls a provider.
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.domain.analysis.schemas import ExecutionEvidenceResponse
from app.domain.analysis.service import (
    AnalysisNotFoundError,
    get_execution_evidence,
)

router = APIRouter(prefix="/executions", tags=["executions"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("/{execution_id}", response_model=ExecutionEvidenceResponse)
async def get_execution_endpoint(
    execution_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ExecutionEvidenceResponse:
    """Serve one execution's persisted analysis + citation evidence."""
    try:
        return await get_execution_evidence(
            session, workspace_id=ctx.workspace_id, analysis_id=execution_id
        )
    except AnalysisNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        ) from exc
