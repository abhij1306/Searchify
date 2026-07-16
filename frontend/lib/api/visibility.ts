/**
 * Visibility domain endpoint (F2): the selected-run dashboard projection —
 * Visibility Score, per-engine comparison, and the brand-vs-competitor rankings
 * table. `sentiment` / `avg_position` are present but nullable at MVP. Defaults
 * to the project's latest completed audit when `audit_id` is omitted. Response
 * passes through `strictValidate`.
 */
import { apiClient, type ApiRequestOptions } from './client';
import { strictValidate, visibilitySchema } from './schemas';
import { definedQuery, withQuery } from './shared';
import type { Visibility } from './types';

export const visibilityApi = {
  getProjectVisibility: async (
    projectId: string,
    params?: { audit_id?: string },
    options?: ApiRequestOptions,
  ) => {
    const path = withQuery(`/projects/${projectId}/visibility`, definedQuery(params));
    const res = await apiClient.get<Visibility>(path, options);
    return strictValidate(visibilitySchema, res, 'visibility.getProjectVisibility');
  },
};
