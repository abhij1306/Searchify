/**
 * Providers domain endpoints (F2): BYOK provider-connections CRUD + connection
 * test + provider-catalog. The API key is write-only — it is sent on create but
 * is **never** present on any response (invariant 6). Every response passes
 * through `strictValidate`.
 */
import { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import {
  providerCatalogSchema,
  providerConnectionSchema,
  strictValidate,
} from './schemas';
import type { ProviderCatalog, ProviderConnection, TransportProvider } from './types';

const connectionListSchema = z.array(providerConnectionSchema);

const connectionTestResultSchema = z.object({
  status: z.enum(['ok', 'failed']),
  message: z.string().nullable(),
});
export type ConnectionTestResult = z.infer<typeof connectionTestResultSchema>;

export type ProviderConnectionInput = {
  transport_provider: TransportProvider;
  api_key: string;
  base_url?: string | null;
  label?: string | null;
};

export const providersApi = {
  listConnections: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<ProviderConnection[]>('/provider-connections', options);
    return strictValidate(connectionListSchema, res, 'providers.listConnections');
  },
  createConnection: async (input: ProviderConnectionInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<ProviderConnection>('/provider-connections', input, options);
    return strictValidate(providerConnectionSchema, res, 'providers.createConnection');
  },
  updateConnection: async (
    connectionId: string,
    input: Partial<ProviderConnectionInput> & { active?: boolean },
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.patch<ProviderConnection>(
      `/provider-connections/${connectionId}`,
      input,
      options,
    );
    return strictValidate(providerConnectionSchema, res, 'providers.updateConnection');
  },
  deleteConnection: (connectionId: string, options?: ApiRequestOptions) =>
    apiClient.delete<void>(`/provider-connections/${connectionId}`, options),
  testConnection: async (connectionId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<ConnectionTestResult>(
      `/provider-connections/${connectionId}/test`,
      undefined,
      options,
    );
    return strictValidate(connectionTestResultSchema, res, 'providers.testConnection');
  },
  getCatalog: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<ProviderCatalog>('/provider-catalog', options);
    return strictValidate(providerCatalogSchema, res, 'providers.getCatalog');
  },
};
