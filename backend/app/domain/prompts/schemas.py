# Prompt-set + prompt request/response schemas (Q3=A; ids string UUID).
#
# The dedicated prompt resource: prompt sets group prompts; each prompt carries
# text/theme/intent + branded/enabled/origin flags. Adapted from the reference
# ``PromptInput`` and extended with the columns Searchify's prompt model adds.
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PromptIntent = Literal[
    "", "discovery", "comparison", "purchase", "service", "local"
]


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
    text: str
    theme: str
    intent: str
    branded: bool
    enabled: bool
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
