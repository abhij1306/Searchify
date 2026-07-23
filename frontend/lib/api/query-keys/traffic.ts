/**
 * Traffic query-key namespace (F2) — isolated by project so one project's
 * dashboard/tables never collide with another's; every filter (window,
 * granularity, sort, cursor) participates in the key so switching a control
 * re-derives the view.
 */
import type { ListFilters } from './shared';

export const trafficKeys = {
  all: ['traffic'] as const,
  dashboard: (projectId: string, filters: ListFilters = {}) =>
    ['traffic', 'dashboard', projectId, filters] as const,
  pages: (projectId: string, filters: ListFilters = {}) =>
    ['traffic', 'pages', projectId, filters] as const,
  queries: (projectId: string, filters: ListFilters = {}) =>
    ['traffic', 'queries', projectId, filters] as const,
};
