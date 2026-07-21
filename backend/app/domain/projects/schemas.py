# Project request/response schemas (all ids string UUID; workspace-scoped).
#
# Adapted from the reference ``schemas/ai_visibility.py``
# (``AiVisibilityProjectCreate/Update``, ``CompetitorInput``) to UUID +
# workspace-scoped Searchify, and aligned to the committed frontend contract
# (``docs/frontend-architecture.md`` §7): brand aliases are carried nested under
# ``brand.aliases`` and the project response embeds its ``prompt_sets``.
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config.brand_profile import (
    BRAND_PROFILE_FIELD_DESCRIPTION,
    BRAND_PROFILE_FIELD_POSITIONING,
    BRAND_PROFILE_FIELD_PRODUCTS_SERVICES,
    BRAND_PROFILE_FIELD_TARGET_AUDIENCE,
    BRAND_PROFILE_PRODUCT_MAX_CHARS,
    BRAND_PROFILE_PRODUCTS_MAX_COUNT,
    BRAND_PROFILE_SOURCE_AI_SUGGESTED,
    BRAND_PROFILE_SOURCE_MANUAL,
    BRAND_PROFILE_SOURCE_WEB_EVIDENCE,
    BRAND_PROFILE_TEXT_MAX_CHARS,
)
from app.core.config.projects import (
    DEFAULT_BENCHMARK_MODE,
    MAX_REPETITIONS,
    MIN_REPETITIONS,
)
from app.core.config.suggestions import brand_suggestion_settings
from app.domain.prompts.schemas import PromptSetResponse

BenchmarkMode = Literal["consumer_like", "controlled_localized", "forced_grounded"]
BrandProfileSource = Literal[
    BRAND_PROFILE_SOURCE_MANUAL,
    BRAND_PROFILE_SOURCE_WEB_EVIDENCE,
    BRAND_PROFILE_SOURCE_AI_SUGGESTED,
]
BrandProfileField = Literal[
    BRAND_PROFILE_FIELD_DESCRIPTION,
    BRAND_PROFILE_FIELD_POSITIONING,
    BRAND_PROFILE_FIELD_PRODUCTS_SERVICES,
    BRAND_PROFILE_FIELD_TARGET_AUDIENCE,
]


# --------------------------------------------------------------------------
# Shared value objects
# --------------------------------------------------------------------------
class BrandInput(BaseModel):
    aliases: list[str] = Field(default_factory=list)


class BrandResponse(BaseModel):
    aliases: list[str] = Field(default_factory=list)


class CompetitorInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class CompetitorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    aliases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class BrandProfileSources(BaseModel):
    description: BrandProfileSource | None = None
    positioning: BrandProfileSource | None = None
    products_services: BrandProfileSource | None = None
    target_audience: BrandProfileSource | None = None


class BrandProfileSourceArtifacts(BaseModel):
    description: uuid.UUID | None = None
    positioning: uuid.UUID | None = None
    products_services: uuid.UUID | None = None
    target_audience: uuid.UUID | None = None


class BrandProfileUpsert(BaseModel):
    """Human-authored partial upsert; every supplied field becomes manual."""

    description: str | None = Field(
        default=None, max_length=BRAND_PROFILE_TEXT_MAX_CHARS
    )
    positioning: str | None = Field(
        default=None, max_length=BRAND_PROFILE_TEXT_MAX_CHARS
    )
    products_services: (
        list[Annotated[str, Field(max_length=BRAND_PROFILE_PRODUCT_MAX_CHARS)]] | None
    ) = Field(default=None, max_length=BRAND_PROFILE_PRODUCTS_MAX_COUNT)
    target_audience: str | None = Field(
        default=None, max_length=BRAND_PROFILE_TEXT_MAX_CHARS
    )


class BrandProfileResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    brand_id: uuid.UUID
    description: str
    positioning: str
    products_services: list[str] = Field(default_factory=list)
    target_audience: str
    sources: BrandProfileSources = Field(default_factory=BrandProfileSources)
    source_artifact_ids: BrandProfileSourceArtifacts = Field(
        default_factory=BrandProfileSourceArtifacts
    )
    created_at: datetime
    updated_at: datetime


class BrandProfileSuggestRequest(BaseModel):
    confirm_send_evidence: bool = False


class BrandProfileDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=BRAND_PROFILE_TEXT_MAX_CHARS)
    positioning: str = Field(default="", max_length=BRAND_PROFILE_TEXT_MAX_CHARS)
    products_services: list[
        Annotated[str, Field(max_length=BRAND_PROFILE_PRODUCT_MAX_CHARS)]
    ] = Field(default_factory=list, max_length=BRAND_PROFILE_PRODUCTS_MAX_COUNT)
    target_audience: str = Field(default="", max_length=BRAND_PROFILE_TEXT_MAX_CHARS)


class BrandProfileSuggestionResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    brand_id: uuid.UUID
    draft: BrandProfileDraft
    model_identity: dict[str, str]
    prompt_template_version: str
    created_at: datetime


class BrandProfileAcceptRequest(BaseModel):
    accepted_fields: list[BrandProfileField] = Field(default_factory=list)
    manual_overrides: BrandProfileUpsert = Field(default_factory=BrandProfileUpsert)


class BrandProfileAcceptResponse(BaseModel):
    profile: BrandProfileResponse
    accepted_fields: list[BrandProfileField] = Field(default_factory=list)
    skipped_manual_fields: list[BrandProfileField] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Project requests
# --------------------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    brand_name: str = Field(default="", max_length=255)
    brand: BrandInput = Field(default_factory=BrandInput)
    website_url: str = Field(default="", max_length=1024)
    owned_domains: list[str] = Field(default_factory=list)
    unintended_domains: list[str] = Field(default_factory=list)
    competitors: list[CompetitorInput] = Field(default_factory=list)
    country_code: str = Field(default="", max_length=8)
    language_code: str = Field(default="", max_length=16)
    benchmark_mode: BenchmarkMode = DEFAULT_BENCHMARK_MODE
    default_repetitions: int = Field(default=3, ge=MIN_REPETITIONS, le=MAX_REPETITIONS)

    @property
    def brand_aliases(self) -> list[str]:
        return self.brand.aliases


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    brand_name: str | None = Field(default=None, max_length=255)
    brand: BrandInput | None = None
    website_url: str | None = Field(default=None, max_length=1024)
    owned_domains: list[str] | None = None
    unintended_domains: list[str] | None = None
    competitors: list[CompetitorInput] | None = None
    country_code: str | None = Field(default=None, max_length=8)
    language_code: str | None = Field(default=None, max_length=16)
    benchmark_mode: BenchmarkMode | None = None
    default_repetitions: int | None = Field(
        default=None, ge=MIN_REPETITIONS, le=MAX_REPETITIONS
    )


# --------------------------------------------------------------------------
# Brand suggestions (stateless — the setup form may be pre-save, so brand
# context travels in the body instead of a project id in the path)
# --------------------------------------------------------------------------
class BrandContextRequest(BaseModel):
    brand_name: str = Field(min_length=1, max_length=255)
    website_url: str = Field(default="", max_length=1024)
    brand_aliases: list[Annotated[str, Field(max_length=255)]] = Field(
        default_factory=list, max_length=50
    )
    country_code: str = Field(default="", max_length=8)
    language_code: str = Field(default="", max_length=16)
    description: str = Field(default="", max_length=BRAND_PROFILE_TEXT_MAX_CHARS)
    positioning: str = Field(default="", max_length=BRAND_PROFILE_TEXT_MAX_CHARS)
    products_services: list[
        Annotated[str, Field(max_length=BRAND_PROFILE_PRODUCT_MAX_CHARS)]
    ] = Field(default_factory=list, max_length=BRAND_PROFILE_PRODUCTS_MAX_COUNT)
    target_audience: str = Field(default="", max_length=BRAND_PROFILE_TEXT_MAX_CHARS)
    # Backend-enforced consent gate (mirrors PromptGenerateRequest): brand
    # evidence is only sent to the default agent when this is true.
    confirm_send_evidence: bool = False
    count: int = Field(
        default_factory=lambda: brand_suggestion_settings.default_count, ge=1
    )


class CompetitorSuggestRequest(BrandContextRequest):
    existing_competitor_names: list[Annotated[str, Field(max_length=255)]] = Field(
        default_factory=list, max_length=200
    )


class OwnedDomainSuggestRequest(BrandContextRequest):
    existing_owned_domains: list[Annotated[str, Field(max_length=255)]] = Field(
        default_factory=list, max_length=200
    )


class CompetitorSuggestResponse(BaseModel):
    competitors: list[CompetitorInput] = Field(default_factory=list)
    dropped_duplicates: int = 0


class OwnedDomainSuggestResponse(BaseModel):
    domains: list[str] = Field(default_factory=list)
    dropped_duplicates: int = 0


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------
class ProjectResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    brand_name: str
    brand: BrandResponse
    website_url: str
    owned_domains: list[str] = Field(default_factory=list)
    unintended_domains: list[str] = Field(default_factory=list)
    competitors: list[CompetitorResponse] = Field(default_factory=list)
    prompt_sets: list[PromptSetResponse] = Field(default_factory=list)
    country_code: str
    language_code: str
    benchmark_mode: str
    default_repetitions: int
    created_at: datetime
    updated_at: datetime
