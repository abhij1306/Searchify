/**
 * Runs (audits) + executions domain endpoints (F2): launch, list, poll detail,
 * cancel, executions list + single-execution evidence, and export URLs. Run
 * progress is polling-first (`getAudit`). Every JSON response passes through
 * `strictValidate`.
 */
import { z } from 'zod';

import { API_BASE_URL, apiClient, type ApiRequestOptions } from './client';
import { auditSchema, executionSchema, strictValidate } from './schemas';
import { definedQuery, withQuery } from './shared';
import type { Audit, Execution, LogicalEngine } from './types';

const auditListSchema = z.array(auditSchema);
const executionListSchema = z.array(executionSchema);

export type LaunchAuditInput = {
  project_id: string;
  prompt_set_id: string;
  engines: LogicalEngine[];
  repetitions: number;
};

export const runsApi = {
  launchAudit: async (input: LaunchAuditInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<Audit>('/audits', input, options);
    return strictValidate(auditSchema, res, 'runs.launchAudit');
  },
  listAudits: async (params?: { project_id?: string }, options?: ApiRequestOptions) => {
    const path = withQuery('/audits', definedQuery(params));
    const res = await apiClient.get<Audit[]>(path, options);
    return strictValidate(auditListSchema, res, 'runs.listAudits');
  },
  getAudit: async (auditId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<Audit>(`/audits/${auditId}`, options);
    return strictValidate(auditSchema, res, 'runs.getAudit');
  },
  cancelAudit: async (auditId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<Audit>(`/audits/${auditId}/cancel`, undefined, options);
    return strictValidate(auditSchema, res, 'runs.cancelAudit');
  },
  listExecutions: async (auditId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<Execution[]>(`/audits/${auditId}/executions`, options);
    return strictValidate(executionListSchema, res, 'runs.listExecutions');
  },
  getExecution: async (executionId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<Execution>(`/executions/${executionId}`, options);
    return strictValidate(executionSchema, res, 'runs.getExecution');
  },
  /** Same-origin export URLs (browser navigation / download links). */
  exportUrl: (auditId: string, format: 'csv' | 'md') =>
    `${API_BASE_URL}/audits/${auditId}/export.${format}`,
  /** Same-origin SSE endpoint (optional; polling is the baseline). */
  eventsUrl: (auditId: string) => `${API_BASE_URL}/audits/${auditId}/events`,
};
