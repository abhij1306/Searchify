/**
 * Projects + workspaces domain endpoints (F2). Workspace-scoped; no `user_id`.
 * Every response passes through `strictValidate`.
 */
import { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import { projectSchema, strictValidate, workspaceSchema } from './schemas';
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
};
