/**
 * Prompts domain endpoints (F2/F7): prompt-set CRUD, individual prompt edits,
 * CSV import (raw file or browser-parsed rows), AI generation via the
 * app-level default agent, and bulk review-status transitions. Every response
 * passes through `strictValidate`.
 */
import { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import {
  promptGenerateResponseSchema,
  promptSchema,
  promptSetSchema,
  strictValidate,
} from './schemas';
import type { Prompt, PromptGenerateResponse, PromptSet, PromptStatus } from './types';

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

export type PromptUpdateInput = Partial<PromptInput> & {
  status?: PromptStatus;
  // Explicit null detaches the prompt from its topic.
  topic_id?: string | null;
};

export type PromptGenerateInput = {
  count?: number;
  // Scope generation to one existing topic; omitted = model proposes topics.
  topic_id?: string;
  intents?: Prompt['intent'][];
  // Backend-enforced consent gate: brand evidence is only sent to the default
  // agent when this is true (422 otherwise).
  confirm_send_evidence: boolean;
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
    input: PromptUpdateInput,
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
   * AI topic/prompt generation via the app-level default agent. Suggestions
   * land as `proposed` (never audit-eligible until accepted). The caller must
   * set `confirm_send_evidence: true` after user consent — the backend
   * enforces it. Errors: 422 invalid, 502 agent/output failure, 503 when no
   * default agent is configured in the backend environment.
   */
  generate: async (
    promptSetId: string,
    input: PromptGenerateInput,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.post<PromptGenerateResponse>(
      `/prompt-sets/${promptSetId}/generate`,
      input,
      options,
    );
    return strictValidate(promptGenerateResponseSchema, res, 'prompts.generate');
  },
  /** Bulk review transition (accept-all / archive-selected). */
  bulkStatus: async (
    promptSetId: string,
    input: { prompt_ids: string[]; status: PromptStatus },
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.post<PromptSet>(
      `/prompt-sets/${promptSetId}/prompts/bulk-status`,
      input,
      options,
    );
    return strictValidate(promptSetSchema, res, 'prompts.bulkStatus');
  },
};
