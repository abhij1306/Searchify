# Brand-suggestions router: stateless AI competitor / owned-domain suggestions
# for the setup form (F6).
#
# No project id in the path — the setup form may be for a brand-new, unsaved
# project, so brand context arrives in the request body and nothing is
# persisted here; the existing project save flow persists whatever the user
# keeps after review. Still workspace-authenticated (never anonymous): the
# endpoints spend agent quota and forward brand data to the provider.
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import WorkspaceContext, require_active_workspace
from app.connectors.agent.client import AgentNotConfiguredError, DefaultAgentClient
from app.connectors.answer_engines.errors import ProviderError
from app.domain.projects.schemas import (
    CompetitorInput,
    CompetitorSuggestRequest,
    CompetitorSuggestResponse,
    OwnedDomainSuggestRequest,
    OwnedDomainSuggestResponse,
)
from app.domain.projects.suggestions import (
    SuggestionOutputError,
    SuggestionValidationError,
    suggest_competitors,
    suggest_owned_domains,
    validate_suggestion_payload,
)

router = APIRouter(prefix="/brand-suggestions", tags=["brand-suggestions"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]


def _resolve_agent() -> DefaultAgentClient:
    """Instantiate the default agent, mapping missing config to 503.

    Kept at the API layer (mirrors ``generate_prompts_endpoint``) so tests can
    monkeypatch ``brand_suggestions.DefaultAgentClient`` and configuration
    errors surface before any provider work.
    """
    try:
        return DefaultAgentClient()
    except AgentNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "agent_not_configured",
                "message": (
                    "No default agent is configured. Set DEFAULT_AGENT_API_KEY "
                    "(or MISTRALAI_API_KEY) in the backend environment."
                ),
            },
        ) from exc


def _raise_invalid(exc: SuggestionValidationError) -> None:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "suggestion_invalid", "message": str(exc)},
    ) from exc


def _raise_unparseable(exc: SuggestionOutputError) -> None:
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"code": "suggestion_unparseable", "message": str(exc)},
    ) from exc


def _raise_agent_failed(exc: ProviderError) -> None:
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"code": "agent_call_failed", "message": str(exc)},
    ) from exc


@router.post(
    "/competitors",
    response_model=CompetitorSuggestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def suggest_competitors_endpoint(
    payload: CompetitorSuggestRequest, _ctx: _WorkspaceDep
) -> CompetitorSuggestResponse:
    """AI competitor suggestions via the app-level default agent.

    Guard order mirrors ``generate_prompts_endpoint`` (minus the 404 scope
    check — there is no persisted resource): confirmation/bounds (422) before
    agent configuration (503), then provider/output failures (502). The
    backend enforces ``confirm_send_evidence``, never just the UI.
    """
    try:
        # 422 before 503: an invalid payload must be rejected as invalid even
        # when no agent is configured (mirrors generate_prompts_endpoint).
        validate_suggestion_payload(payload)
    except SuggestionValidationError as exc:
        _raise_invalid(exc)
    agent = _resolve_agent()
    try:
        competitors, dropped = await suggest_competitors(payload=payload, agent=agent)
    except SuggestionValidationError as exc:
        _raise_invalid(exc)
    except SuggestionOutputError as exc:
        _raise_unparseable(exc)
    except ProviderError as exc:
        _raise_agent_failed(exc)
    return CompetitorSuggestResponse(
        competitors=[
            CompetitorInput(name=c.name, aliases=c.aliases, domains=c.domains)
            for c in competitors
        ],
        dropped_duplicates=dropped,
    )


@router.post(
    "/owned-domains",
    response_model=OwnedDomainSuggestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def suggest_owned_domains_endpoint(
    payload: OwnedDomainSuggestRequest, _ctx: _WorkspaceDep
) -> OwnedDomainSuggestResponse:
    """AI owned-domain suggestions via the app-level default agent.

    Same guard order as ``suggest_competitors_endpoint``. Only first-party
    owned domains — never competitor or unintended domains (system prompt +
    server-side normalization).
    """
    try:
        # 422 before 503: an invalid payload must be rejected as invalid even
        # when no agent is configured (mirrors generate_prompts_endpoint).
        validate_suggestion_payload(payload)
    except SuggestionValidationError as exc:
        _raise_invalid(exc)
    agent = _resolve_agent()
    try:
        domains, dropped = await suggest_owned_domains(payload=payload, agent=agent)
    except SuggestionValidationError as exc:
        _raise_invalid(exc)
    except SuggestionOutputError as exc:
        _raise_unparseable(exc)
    except ProviderError as exc:
        _raise_agent_failed(exc)
    return OwnedDomainSuggestResponse(domains=domains, dropped_duplicates=dropped)
