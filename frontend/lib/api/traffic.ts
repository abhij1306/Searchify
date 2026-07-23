/**
 * Traffic domain endpoints (F2): the headline traffic projection, paged
 * per-page / per-query stat rows, and the sync pass-through that enqueues
 * integrations sync runs.
 *
 * Owns transport for the traffic slice. Read endpoints render persisted
 * projections only (invariant 7 — no recomputation, no provider calls); the
 * backend serves an empty payload when no snapshot exists for the requested
 * window. Series points are nullable so unavailable buckets render as chart
 * gaps, never invented zeros. Every JSON response passes through
 * `strictValidate` (fail loud on any drift). All paths are relative
 * `/api/v1` (same-origin proxy, invariant 12).
 */
import type { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import {
  strictValidate,
  trafficDashboardSchema,
  trafficPagesPageSchema,
  trafficQueriesPageSchema,
  trafficSyncEnqueueResponseSchema,
  type snapshotGranularitySchema,
} from './schemas';
import { definedQuery, withQuery } from './shared';

export type SnapshotGranularity = z.infer<typeof snapshotGranularitySchema>;
export type TrafficDashboard = z.infer<typeof trafficDashboardSchema>;
export type TrafficPageRow = z.infer<typeof trafficPagesPageSchema>['items'][number];
export type TrafficPagesPage = z.infer<typeof trafficPagesPageSchema>;
export type TrafficQueryRow = z.infer<typeof trafficQueriesPageSchema>['items'][number];
export type TrafficQueriesPage = z.infer<typeof trafficQueriesPageSchema>;
export type TrafficSyncEnqueueResponse = z.infer<typeof trafficSyncEnqueueResponseSchema>;

/** Headline window query (`from`/`to` ISO dates, snapshot granularity). */
export type TrafficWindowParams = {
  from?: string;
  to?: string;
  granularity?: SnapshotGranularity;
};

/**
 * Keyset table query (C4). `sort` is a backend-config whitelist value
 * (`422` on anything else); the frontend never hard-codes the whitelist.
 */
export type TrafficTableParams = {
  from?: string;
  to?: string;
  sort?: string;
  cursor?: string;
};

export const trafficApi = {
  getTraffic: async (projectId: string, params?: TrafficWindowParams, options?: ApiRequestOptions) => {
    const path = withQuery(`/projects/${projectId}/traffic`, definedQuery(params));
    const res = await apiClient.get<TrafficDashboard>(path, options);
    return strictValidate(trafficDashboardSchema, res, 'traffic.getTraffic');
  },
  getPages: async (projectId: string, params?: TrafficTableParams, options?: ApiRequestOptions) => {
    const path = withQuery(`/projects/${projectId}/traffic/pages`, definedQuery(params));
    const res = await apiClient.get<TrafficPagesPage>(path, options);
    return strictValidate(trafficPagesPageSchema, res, 'traffic.getPages');
  },
  getQueries: async (
    projectId: string,
    params?: TrafficTableParams,
    options?: ApiRequestOptions,
  ) => {
    const path = withQuery(`/projects/${projectId}/traffic/queries`, definedQuery(params));
    const res = await apiClient.get<TrafficQueriesPage>(path, options);
    return strictValidate(trafficQueriesPageSchema, res, 'traffic.getQueries');
  },
  /**
   * `POST /projects/{id}/traffic/sync` — enqueues one integrations sync run
   * per active mapped GSC/GA4 connection of the project (202, C3: one
   * `{sync_run_id, connection_id, status}` object per queued run). Poll each
   * run via `integrationsApi.getSync(connection_id, sync_run_id)`.
   */
  syncNow: async (projectId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<TrafficSyncEnqueueResponse>(
      `/projects/${projectId}/traffic/sync`,
      undefined,
      options,
    );
    return strictValidate(trafficSyncEnqueueResponseSchema, res, 'traffic.syncNow');
  },
};
