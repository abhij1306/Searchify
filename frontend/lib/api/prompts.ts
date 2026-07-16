/**
 * Prompts domain endpoints (F2/F7): prompt-set CRUD, individual prompt edits,
 * CSV import (raw file or browser-parsed rows), and the AI-suggest `/generate`
 * stub (coming-soon UI). Every response passes through `strictValidate`.
 */
import { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import { promptSchema, promptSetSchema, strictValidate } from './schemas';
import type { Prompt, PromptSet } from './types';

const promptSetListSchema = z.array(promptSetSchema);

export type PromptInput = {
  text: string;
  // Backend `PromptInput.theme` is a non-null `str = ""` — create/import 422 on
  // null. Send an empty string (never null) when unset.
  theme?: string;
  intent: Prompt['intent'];
  branded: boolean;
  enabled: boolean;
};

export const promptsApi = {
  listPromptSets: async (projectId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<PromptSet[]>(
      `/prompt-sets?project_id=${encodeURIComponent(projectId)}`,
      options,
    );
    return strictValidate(promptSetListSchema, res, 'prompts.listPromptSets');
  },
  getPromptSet: async (promptSetId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<PromptSet>(`/prompt-sets/${promptSetId}`, options);
    return strictValidate(promptSetSchema, res, 'prompts.getPromptSet');
  },
  createPromptSet: async (
    input: { project_id: string; name?: string; description?: string },
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.post<PromptSet>('/prompt-sets', input, options);
    return strictValidate(promptSetSchema, res, 'prompts.createPromptSet');
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
  /**
   * Persist browser-parsed rows through the same `/import` endpoint. The B3
   * backend accepts a JSON body of `{ prompts: [...] }` (rows already parsed +
   * previewed in the browser) and bulk-creates them with `origin='imported'`.
   */
  importRows: async (
    promptSetId: string,
    rows: PromptInput[],
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.post<PromptSet>(
      `/prompt-sets/${promptSetId}/import`,
      { prompts: rows },
      options,
    );
    return strictValidate(promptSetSchema, res, 'prompts.importRows');
  },
  /**
   * AI-suggest stub (B-4). The backend returns 501 `not_implemented`, so this
   * call is expected to throw an `ApiError`; the coming-soon UI does not invoke
   * it eagerly. Kept here so the panel can wire a probe when the roadmap lands.
   */
  generate: (promptSetId: string, options?: ApiRequestOptions) =>
    apiClient.post<void>(`/prompt-sets/${promptSetId}/generate`, undefined, options),
};
