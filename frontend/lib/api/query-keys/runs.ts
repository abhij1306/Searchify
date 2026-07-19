/** Runs (audits + executions) + visibility query-key namespaces. */
import type { ListFilters } from './shared';

export const runKeys = {
  all: ['runs'] as const,
  list: (filters: ListFilters = {}) => ['runs', 'list', filters] as const,
  detail: (auditId: string) => ['runs', 'detail', auditId] as const,
  executions: (auditId: string) => ['runs', 'executions', auditId] as const,
  execution: (executionId: string) => ['runs', 'execution', executionId] as const,
};

export const visibilityKeys = {
  all: ['visibility'] as const,
  project: (projectId: string, auditId?: string, filters: ListFilters = {}) =>
    ['visibility', 'project', projectId, auditId ?? 'latest', filters] as const,
  // Cross-run trend series: every filter (engine, from, to, granularity)
  // participates in the key so switching a control re-derives the view.
  trends: (projectId: string, filters: ListFilters = {}) =>
    ['visibility', 'trends', projectId, filters] as const,
  // Shared execution-evidence dataset for the Mentions & Citations and Query
  // Fanout tabs. ONE identical key is used by both tabs so switching between
  // them reuses the cache instead of refetching. Every shared filter
  // (audit_id, prompt_id, engine, from, to, limit) participates in the key.
  evidence: (projectId: string, filters: ListFilters = {}) =>
    ['visibility', 'evidence', projectId, filters] as const,
};
