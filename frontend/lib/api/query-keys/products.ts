/** Products (agentic commerce) query-key namespace. */
import type { ListFilters } from './shared';

export const productKeys = {
  all: ['products'] as const,
  list: (projectId: string) => ['products', 'list', projectId] as const,
  detail: (productId: string) => ['products', 'detail', productId] as const,
  competitorProducts: (projectId: string) =>
    ['products', 'competitor-products', projectId] as const,
  // `auditId ?? 'latest'` mirrors the backend default-to-latest resolution so
  // the unfiltered view and an explicit selection cache separately; the engine
  // slice participates so switching engines re-derives the view.
  visibility: (projectId: string, auditId?: string, engine?: string) =>
    ['products', 'visibility', projectId, auditId ?? 'latest', engine ?? 'all'] as const,
  // Every filter (audit_id, engine, limit) participates in the key so
  // switching a control re-derives the view.
  evidence: (productId: string, filters: ListFilters = {}) =>
    ['products', 'evidence', productId, filters] as const,
};
