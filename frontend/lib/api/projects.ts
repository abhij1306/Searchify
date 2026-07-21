/**
 * Projects + workspaces domain endpoints (F2). Workspace-scoped; no `user_id`.
 * Every response passes through `strictValidate`.
 */
import { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import {
  competitorSuggestResponseSchema,
  ownedDomainSuggestResponseSchema,
  projectSchema,
  strictValidate,
  workspaceSchema,
} from './schemas';
import type { Project, Workspace } from './types';

const workspaceListSchema = z.array(workspaceSchema);
const projectListSchema = z.array(projectSchema);

export type ProjectInput = {
  name: string;
  brand_name: string;
  website_url: string;
  country_code: string;
  language_code: string;
  benchmark_mode: Project['benchmark_mode'];
  default_repetitions: number;
  brand: { aliases: string[] };
  owned_domains: string[];
  unintended_domains: string[];
  competitors: Array<{ name: string; aliases: string[]; domains: string[] }>;
};

/** Shared brand context for the stateless `/brand-suggestions/*` endpoints. */
type BrandSuggestBase = {
  brand_name: string;
  website_url?: string;
  brand_aliases?: string[];
  country_code?: string;
  language_code?: string;
  count?: number;
  // Backend-enforced consent gate: brand evidence is only sent to the default
  // agent when this is true (422 otherwise).
  confirm_send_evidence: boolean;
};

export type CompetitorSuggestInput = BrandSuggestBase & {
  existing_competitor_names?: string[];
};

export type OwnedDomainSuggestInput = BrandSuggestBase & {
  existing_owned_domains?: string[];
};

export type CompetitorSuggestResponse = z.infer<typeof competitorSuggestResponseSchema>;
export type OwnedDomainSuggestResponse = z.infer<typeof ownedDomainSuggestResponseSchema>;

export const projectsApi = {
  listWorkspaces: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<Workspace[]>('/workspaces', options);
    return strictValidate(workspaceListSchema, res, 'projects.listWorkspaces');
  },
  listProjects: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<Project[]>('/projects', options);
    return strictValidate(projectListSchema, res, 'projects.listProjects');
  },
  getProject: async (projectId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<Project>(`/projects/${projectId}`, options);
    return strictValidate(projectSchema, res, 'projects.getProject');
  },
  createProject: async (input: ProjectInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<Project>('/projects', input, options);
    return strictValidate(projectSchema, res, 'projects.createProject');
  },
  updateProject: async (
    projectId: string,
    input: Partial<ProjectInput>,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.patch<Project>(`/projects/${projectId}`, input, options);
    return strictValidate(projectSchema, res, 'projects.updateProject');
  },
  deleteProject: (projectId: string, options?: ApiRequestOptions) =>
    apiClient.delete<void>(`/projects/${projectId}`, options),
  /**
   * AI competitor / owned-domain suggestions for the setup form via the
   * app-level default agent. Stateless: brand context travels in the body (the
   * project may not exist yet) and nothing is persisted — suggestions fill the
   * form for review and the normal save flow persists. The caller must set
   * `confirm_send_evidence: true` after user consent — the backend enforces
   * it. Errors: 422 invalid, 502 agent/output failure, 503 no default agent.
   */
  suggestCompetitors: async (input: CompetitorSuggestInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<CompetitorSuggestResponse>(
      '/brand-suggestions/competitors',
      input,
      options,
    );
    return strictValidate(competitorSuggestResponseSchema, res, 'projects.suggestCompetitors');
  },
  suggestOwnedDomains: async (input: OwnedDomainSuggestInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<OwnedDomainSuggestResponse>(
      '/brand-suggestions/owned-domains',
      input,
      options,
    );
    return strictValidate(ownedDomainSuggestResponseSchema, res, 'projects.suggestOwnedDomains');
  },
};
