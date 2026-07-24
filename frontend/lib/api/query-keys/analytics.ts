/**
 * LLM Analytics query-key namespace (F3) — isolated by project; every filter
 * (window, granularity, source, cursor) participates in the key so switching
 * a control re-derives the view.
 */
import type { ListFilters } from './shared';

export const analyticsKeys = {
  all: ['analytics'] as const,
  dashboard: (projectId: string, filters: ListFilters = {}) =>
    ['analytics', 'dashboard', projectId, filters] as const,
  referrals: (projectId: string, filters: ListFilters = {}) =>
    ['analytics', 'referrals', projectId, filters] as const,
  themes: (projectId: string, filters: ListFilters = {}) =>
    ['analytics', 'themes', projectId, filters] as const,
};
