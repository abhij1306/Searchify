# Prompt-set / prompt ORM -> response DTO mappers.
#
# Kept separate from the service so both the prompts router and the projects
# router (which embeds prompt sets in the project response) share one mapping.
from __future__ import annotations

from app.domain.prompts.schemas import PromptResponse, PromptSetResponse
from app.models.prompt import Prompt, PromptSet


def prompt_to_response(prompt: Prompt) -> PromptResponse:
    return PromptResponse.model_validate(prompt)


def prompt_set_to_response(prompt_set: PromptSet) -> PromptSetResponse:
    prompts = [prompt_to_response(p) for p in prompt_set.prompts]
    return PromptSetResponse(
        id=prompt_set.id,
        project_id=prompt_set.project_id,
        name=prompt_set.name,
        description=prompt_set.description,
        prompts=prompts,
        prompt_count=len(prompts),
        created_at=prompt_set.created_at,
        updated_at=prompt_set.updated_at,
    )
