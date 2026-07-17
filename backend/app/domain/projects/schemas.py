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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.config.projects import (
    DEFAULT_BENCHMARK_MODE,
    MAX_REPETITIONS,
    MIN_REPETITIONS,
)
from app.domain.prompts.schemas import PromptSetResponse

BenchmarkMode = Literal["consumer_like", "controlled_localized", "forced_grounded"]


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
