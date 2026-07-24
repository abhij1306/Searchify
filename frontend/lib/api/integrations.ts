/**
 * Integrations domain endpoints (F1): GSC/GA4/Bing connection management —
 * list, test, sync, sync-run history/detail, disconnect — plus the OAuth
 * start URL used for full-page 302 navigation.
 *
 * Owns transport for the integrations slice. Every JSON response passes
 * through `strictValidate` (fail loud on any drift). All paths are relative
 * `/api/v1` (same-origin proxy, invariant 12). Tokens are Fernet-encrypted
 * on the backend grant and are NEVER present on any response — the
 * `.strict()` connection schema throws on any leaked token key
 * (invariant 6).
 */
import type { z } from 'zod';

import { API_BASE_URL, apiClient, type ApiRequestOptions } from './client';
import {
  integrationConnectionListSchema,
  integrationSyncEnqueueSchema,
  integrationSyncRunListSchema,
  integrationSyncRunSchema,
  integrationTestResultSchema,
  strictValidate,
  type integrationConnectionSchema,
  type integrationProviderSchema,
} from './schemas';

export type IntegrationProvider = z.infer<typeof integrationProviderSchema>;
export type IntegrationConnection = z.infer<typeof integrationConnectionSchema>;
export type IntegrationTestResult = z.infer<typeof integrationTestResultSchema>;
export type IntegrationSyncEnqueue = z.infer<typeof integrationSyncEnqueueSchema>;
export type IntegrationSyncRun = z.infer<typeof integrationSyncRunSchema>;

/** Optional window body for `POST /integrations/{id}/sync` (ISO dates). */
export type SyncWindowInput = {
  window_start?: string;
  window_end?: string;
};

export const integrationsApi = {
  list: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<IntegrationConnection[]>('/integrations', options);
    return strictValidate(integrationConnectionListSchema, res, 'integrations.list');
  },
  test: async (connectionId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<IntegrationTestResult>(
      `/integrations/${connectionId}/test`,
      undefined,
      options,
    );
    return strictValidate(integrationTestResultSchema, res, 'integrations.test');
  },
  sync: async (connectionId: string, input?: SyncWindowInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<IntegrationSyncEnqueue>(
      `/integrations/${connectionId}/sync`,
      input,
      options,
    );
    return strictValidate(integrationSyncEnqueueSchema, res, 'integrations.sync');
  },
  listSyncs: async (connectionId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<IntegrationSyncRun[]>(
      `/integrations/${connectionId}/syncs`,
      options,
    );
    return strictValidate(integrationSyncRunListSchema, res, 'integrations.listSyncs');
  },
  getSync: async (connectionId: string, syncId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<IntegrationSyncRun>(
      `/integrations/${connectionId}/syncs/${syncId}`,
      options,
    );
    return strictValidate(integrationSyncRunSchema, res, 'integrations.getSync');
  },
  delete: (connectionId: string, options?: ApiRequestOptions) =>
    apiClient.delete<void>(`/integrations/${connectionId}`, options),
  /**
   * Same-origin OAuth start URL (a 302 endpoint). Used with a full-page
   * navigation (`assignLocation`), NEVER through `apiClient` — the browser
   * follows the redirect to the provider consent screen through the
   * same-origin proxy (invariant 12).
   */
  oauthStartUrl: (provider: IntegrationProvider) =>
    `${API_BASE_URL}/integrations/oauth/${provider}/start`,
};
