# Prompt-set + prompt request/response schemas (Q3=A; ids string UUID).
#
# The dedicated prompt resource: prompt sets group prompts; each prompt carries
# text/theme/intent + branded/enabled/origin flags. Adapted from the reference
# ``PromptInput`` and extended with the columns Searchify's prompt model adds.
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from app.core.config.prompts import PROMPT_STATUSES, prompt_generation_settings

PromptIntent = Literal["", "discovery", "comparison", "purchase", "service", "local"]
# ``Literal`` requires inline literals for static checkers, so the values are
# repeated here; this guard keeps the alias in lock-step with the config
# constants (PROMPT_STATUS_*) so they cannot drift silently.
PromptStatus = Literal["proposed", "active", "archived"]
assert set(get_args(PromptStatus)) == PROMPT_STATUSES


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
class PromptInput(BaseModel):
    """A single prompt on create/import. ``intent`` is validated + casefolded
    by the service; an unknown intent normalizes to ``""``."""

    text: str = Field(min_length=1)
    theme: str = Field(default="", max_length=255)
    intent: str = Field(default="")
    branded: bool = False
    enabled: bool = True


class PromptCreate(PromptInput):
    prompt_set_id: uuid.UUID


class PromptUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1)
    theme: str | None = Field(default=None, max_length=255)
    intent: str | None = None
    branded: bool | None = None
    enabled: bool | None = None
    status: PromptStatus | None = None
    topic_id: uuid.UUID | None = None


class PromptImport(BaseModel):
    """MVP CSV bulk-create payload: already-parsed prompt rows.

    The browser parses the CSV at F7 and posts the rows here through the normal
    import path; the service persists them via the prompt resource with
    ``origin='imported'``.
    """

    prompts: list[PromptInput] = Field(default_factory=list)


class PromptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prompt_set_id: uuid.UUID
    topic_id: uuid.UUID | None = None
    text: str
    theme: str
    intent: str
    branded: bool
    enabled: bool
    status: str
    origin: str
    generation_evidence: dict | None = None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Prompt sets
# --------------------------------------------------------------------------
class PromptSetCreate(BaseModel):
    project_id: uuid.UUID
    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=1024)


class PromptSetUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1024)


class PromptSetResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str = ""
    prompts: list[PromptResponse] = Field(default_factory=list)
    prompt_count: int = 0
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Topics
# --------------------------------------------------------------------------
class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=1024)


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)


class TopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str
    origin: str
    # Per-status prompt counts for the topics rail (projection, computed).
    active_count: int = 0
    proposed_count: int = 0
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# AI generation + bulk review
# --------------------------------------------------------------------------
class PromptGenerateRequest(BaseModel):
    """Body for ``POST /prompt-sets/{id}/generate``.

    ``confirm_send_evidence`` must be true — the backend (not just the UI)
    gates sending brand evidence to the default agent. ``topic_id`` scopes
    generation to one existing topic.
    """

    count: int = Field(
        default_factory=lambda: prompt_generation_settings.default_count, ge=1
    )
    topic_id: uuid.UUID | None = None
    intents: list[PromptIntent] = Field(default_factory=list)
    confirm_send_evidence: bool = False


class PromptGenerateResponse(BaseModel):
    generated: list[PromptResponse] = Field(default_factory=list)
    topics: list[TopicResponse] = Field(default_factory=list)
    # Total prompts dropped as duplicates: intra-response collapses (an
    # equivalent text repeated within one model response) plus DB
    # ``ON CONFLICT`` skips against pre-existing prompts in the set.
    dropped_duplicates: int = 0


class PromptBulkStatusRequest(BaseModel):
    """Bulk review transition (accept-all / archive-selected)."""

    prompt_ids: list[uuid.UUID] = Field(min_length=1)
    status: PromptStatus
