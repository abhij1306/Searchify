/**
 * Prompts domain endpoints (F2): prompt-set CRUD, individual prompt edits, CSV
 * import, and the AI-suggest `/generate` stub (coming-soon UI). Every response
 * passes through `strictValidate`.
 */
import { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import { promptSchema, promptSetSchema, strictValidate } from './schemas';
import type { Prompt, PromptSet } from './types';

const promptSetListSchema = z.array(promptSetSchema);

export type PromptInput = {
  text: string;
  theme?: string | null;
  intent: Prompt['intent'];
  branded: boolean;
  enabled: boolean;
};

export const promptsApi = {
  listPromptSets: async (projectId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<PromptSet[]>(`/projects/${projectId}/prompt-sets`, options);
    return strictValidate(promptSetListSchema, res, 'prompts.listPromptSets');
  },
  getPromptSet: async (promptSetId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<PromptSet>(`/prompt-sets/${promptSetId}`, options);
    return strictValidate(promptSetSchema, res, 'prompts.getPromptSet');
  },
  createPrompt: async (promptSetId: string, input: PromptInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<Prompt>(`/prompt-sets/${promptSetId}/prompts`, input, options);
    return strictValidate(promptSchema, res, 'prompts.createPrompt');
  },
  updatePrompt: async (
    promptId: string,
    input: Partial<PromptInput>,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.patch<Prompt>(`/prompts/${promptId}`, input, options);
    return strictValidate(promptSchema, res, 'prompts.updatePrompt');
  },
  deletePrompt: (promptId: string, options?: ApiRequestOptions) =>
    apiClient.delete<void>(`/prompts/${promptId}`, options),
  importCsv: async (promptSetId: string, file: File, options?: ApiRequestOptions) => {
    const form = new FormData();
    form.append('file', file);
    const res = await apiClient.postForm<PromptSet>(
      `/prompt-sets/${promptSetId}/import`,
      form,
      options,
    );
    return strictValidate(promptSetSchema, res, 'prompts.importCsv');
  },
  generate: async (promptSetId: string, options?: ApiRequestOptions) => {
    // AI-suggest stub → coming-soon UI. Returns the (unchanged) prompt set.
    const res = await apiClient.post<PromptSet>(
      `/prompt-sets/${promptSetId}/generate`,
      undefined,
      options,
    );
    return strictValidate(promptSetSchema, res, 'prompts.generate');
  },
};
