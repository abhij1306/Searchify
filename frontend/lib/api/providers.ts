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
import type {
  LogicalEngine,
  ProviderCatalog,
  ProviderConnection,
  TransportProvider,
} from './types';

const connectionListSchema = z.array(providerConnectionSchema);

// Mirrors B4's `ProviderConnectionTestResponse`. `status` is a free string on
// the wire ('ok' | 'failed'); the extra provenance fields are surfaced inline.
const connectionTestResultSchema = z.object({
  connection_id: z.string().uuid(),
  status: z.string(),
  error_code: z.string().optional().default(''),
  detail: z.string().optional().default(''),
  latency_ms: z.number().nullable().optional(),
  logical_engine: z.string().optional().default(''),
  transport_provider: z.string().optional().default(''),
  transport_model: z.string().optional().default(''),
  tested_at: z.string(),
});
type ConnectionTestResult = z.infer<typeof connectionTestResultSchema>;

/** A route entry sent on create/update (B4 `ProviderRouteInput`). */
export type ProviderRouteInput = {
  logical_engine: LogicalEngine;
  transport_model?: string;
  is_default?: boolean;
};

type ProviderConnectionInput = {
  transport_provider: TransportProvider;
  api_key: string;
  base_url?: string;
  label?: string;
  active?: boolean;
  routes?: ProviderRouteInput[];
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

// Re-export so the logical-engine literal union is importable without
// reaching into `schemas`.
export type { LogicalEngine };
