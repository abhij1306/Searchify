/**
 * Site Health domain endpoints + query/mutation options (F2).
 *
 * Owns transport for the Site Health slice: entitlements, crawl create/list/
 * get/cancel, progressive keyset inventory, the persistent monitored set,
 * analyzed pages, issues, events, per-URL rerun, and same-origin export/stream
 * URLs. Every JSON response passes through `strictValidate` (fail loud on any
 * drift). All paths are relative `/api/v1` (same-origin proxy, invariant 12)
 * and every read accepts an `AbortSignal` via `ApiRequestOptions`.
 *
 * The frontend never invents a discovered total: count-bearing fields the
 * backend redacts for Free arrive `null`/absent and are validated as such.
 */
import { queryOptions, mutationOptions } from '@tanstack/react-query';

import { API_BASE_URL, apiClient, type ApiRequestOptions } from './client';
import { queryKeys } from './query-keys';
import {
  inventoryPageSchema,
  monitoredUrlsResponseSchema,
  pageDetailSchema,
  pagesPageSchema,
  siteCrawlListPageSchema,
  siteCrawlSchema,
  siteHealthDashboardSchema,
  siteHealthEntitlementSchema,
  siteIssueDetailSchema,
  siteIssuesPageSchema,
  strictValidate,
} from './schemas';
import { definedQuery, withQuery } from './shared';
import type {
  InventoryPage,
  MonitoredUrlsResponse,
  PageDetail,
  PagesPage,
  SiteCrawl,
  SiteCrawlListPage,
  SiteHealthDashboard,
  SiteHealthEntitlement,
  SiteIssueDetail,
  SiteIssuesPage,
} from './types';

/** `POST /site-crawls` body. Workspace is resolved from `X-Workspace-Id`. */
export type CreateCrawlInput = {
  project_id: string;
  include_globs?: string[];
  exclude_globs?: string[];
  /**
   * Optional deterministic 64-bit seed as a decimal string. The backend
   * create contract names this `seed` (it aliases the model's `random_seed`),
   * so the wire field must be `seed`.
   */
  seed?: string;
};

/** Keyset inventory query params. `limit<=200`, ordering is URL-only. */
export type InventoryParams = {
  cursor?: string;
  limit?: number;
  query?: string;
  status?: string;
  monitored?: boolean;
};

export type CrawlListParams = { project_id: string; limit?: number; cursor?: string };
export type PagesParams = { cursor?: string; limit?: number; status?: string; monitored?: boolean };
export type IssuesParams = {
  cursor?: string;
  limit?: number;
  severity?: string;
  category?: string;
  dimension?: string;
  rule_id?: string;
  site_url_id?: string;
};

/** `PUT /projects/{id}/monitored-urls` body — atomic full-set replacement. */
export type ReplaceMonitoredInput = {
  site_url_ids: string[];
  expected_selection_version: number;
};

export const siteHealthApi = {
  getEntitlements: async (options?: ApiRequestOptions) => {
    const res = await apiClient.get<SiteHealthEntitlement>('/entitlements', options);
    return strictValidate(siteHealthEntitlementSchema, res, 'siteHealth.getEntitlements');
  },
  createCrawl: async (input: CreateCrawlInput, options?: ApiRequestOptions) => {
    const res = await apiClient.post<SiteCrawl>('/site-crawls', input, options);
    return strictValidate(siteCrawlSchema, res, 'siteHealth.createCrawl');
  },
  listCrawls: async (params: CrawlListParams, options?: ApiRequestOptions) => {
    const path = withQuery('/site-crawls', definedQuery(params));
    const res = await apiClient.get<SiteCrawlListPage>(path, options);
    return strictValidate(siteCrawlListPageSchema, res, 'siteHealth.listCrawls');
  },
  getCrawl: async (crawlId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<SiteCrawl>(`/site-crawls/${crawlId}`, options);
    return strictValidate(siteCrawlSchema, res, 'siteHealth.getCrawl');
  },
  cancelCrawl: async (crawlId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.post<SiteCrawl>(`/site-crawls/${crawlId}/cancel`, undefined, options);
    return strictValidate(siteCrawlSchema, res, 'siteHealth.cancelCrawl');
  },
  getInventory: async (crawlId: string, params?: InventoryParams, options?: ApiRequestOptions) => {
    const path = withQuery(`/site-crawls/${crawlId}/inventory`, definedQuery(params));
    const res = await apiClient.get<InventoryPage>(path, options);
    return strictValidate(inventoryPageSchema, res, 'siteHealth.getInventory');
  },
  getMonitoredUrls: async (projectId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<MonitoredUrlsResponse>(
      `/projects/${projectId}/monitored-urls`,
      options,
    );
    return strictValidate(monitoredUrlsResponseSchema, res, 'siteHealth.getMonitoredUrls');
  },
  replaceMonitoredUrls: async (
    projectId: string,
    input: ReplaceMonitoredInput,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.put<MonitoredUrlsResponse>(
      `/projects/${projectId}/monitored-urls`,
      input,
      options,
    );
    return strictValidate(monitoredUrlsResponseSchema, res, 'siteHealth.replaceMonitoredUrls');
  },
  getPages: async (crawlId: string, params?: PagesParams, options?: ApiRequestOptions) => {
    const path = withQuery(`/site-crawls/${crawlId}/pages`, definedQuery(params));
    const res = await apiClient.get<PagesPage>(path, options);
    return strictValidate(pagesPageSchema, res, 'siteHealth.getPages');
  },
  getPage: async (crawlId: string, siteUrlId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<PageDetail>(
      `/site-crawls/${crawlId}/pages/${siteUrlId}`,
      options,
    );
    return strictValidate(pageDetailSchema, res, 'siteHealth.getPage');
  },
  rerunPage: async (crawlId: string, siteUrlId: string, options?: ApiRequestOptions) => {
    // 202 Accepted with an empty body; nothing to validate.
    await apiClient.post<void>(`/site-crawls/${crawlId}/pages/${siteUrlId}/rerun`, undefined, options);
  },
  getIssues: async (crawlId: string, params?: IssuesParams, options?: ApiRequestOptions) => {
    const path = withQuery(`/site-crawls/${crawlId}/issues`, definedQuery(params));
    const res = await apiClient.get<SiteIssuesPage>(path, options);
    return strictValidate(siteIssuesPageSchema, res, 'siteHealth.getIssues');
  },
  getIssue: async (crawlId: string, issueId: string, options?: ApiRequestOptions) => {
    const res = await apiClient.get<SiteIssueDetail>(
      `/site-crawls/${crawlId}/issues/${issueId}`,
      options,
    );
    return strictValidate(siteIssueDetailSchema, res, 'siteHealth.getIssue');
  },
  getDashboard: async (projectId: string, crawlId?: string, options?: ApiRequestOptions) => {
    const path = withQuery(
      `/projects/${projectId}/site-health`,
      definedQuery({ crawl_id: crawlId }),
    );
    const res = await apiClient.get<SiteHealthDashboard>(path, options);
    return strictValidate(siteHealthDashboardSchema, res, 'siteHealth.getDashboard');
  },
  /** Same-origin SSE endpoint (polling is the baseline; `?stream=true`). */
  eventsUrl: (crawlId: string) => `${API_BASE_URL}/site-crawls/${crawlId}/events?stream=true`,
  /** Same-origin export URLs (browser navigation / download links). */
  exportUrl: (crawlId: string, format: 'csv' | 'md', view?: 'inventory' | 'pages' | 'issues') => {
    const base = `${API_BASE_URL}/site-crawls/${crawlId}/export.${format}`;
    return format === 'csv' && view ? `${base}?view=${view}` : base;
  },
};

/**
 * React Query option factories. Screens (Tasks 7/8) pass these straight to
 * `useQuery` / `useMutation`, so the query key ↔ endpoint pairing lives in one
 * place. Every `queryFn` forwards the abort signal.
 */
export const siteHealthQueries = {
  entitlements: () =>
    queryOptions({
      queryKey: queryKeys.siteHealth.entitlements(),
      queryFn: ({ signal }) => siteHealthApi.getEntitlements({ signal }),
    }),
  dashboard: (projectId: string, crawlId?: string) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.dashboard(projectId, crawlId),
      queryFn: ({ signal }) => siteHealthApi.getDashboard(projectId, crawlId, { signal }),
    }),
  crawls: (params: CrawlListParams) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.crawls(params.project_id, {
        limit: params.limit ?? null,
        cursor: params.cursor ?? null,
      }),
      queryFn: ({ signal }) => siteHealthApi.listCrawls(params, { signal }),
    }),
  crawl: (crawlId: string) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.crawl(crawlId),
      queryFn: ({ signal }) => siteHealthApi.getCrawl(crawlId, { signal }),
    }),
  inventory: (crawlId: string, params?: InventoryParams) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.inventory(crawlId, {
        cursor: params?.cursor ?? null,
        limit: params?.limit ?? null,
        query: params?.query ?? null,
        status: params?.status ?? null,
        monitored: params?.monitored ?? null,
      }),
      queryFn: ({ signal }) => siteHealthApi.getInventory(crawlId, params, { signal }),
    }),
  monitored: (projectId: string) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.monitored(projectId),
      queryFn: ({ signal }) => siteHealthApi.getMonitoredUrls(projectId, { signal }),
    }),
  pages: (crawlId: string, params?: PagesParams) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.pages(crawlId, {
        cursor: params?.cursor ?? null,
        limit: params?.limit ?? null,
        status: params?.status ?? null,
        monitored: params?.monitored ?? null,
      }),
      queryFn: ({ signal }) => siteHealthApi.getPages(crawlId, params, { signal }),
    }),
  page: (crawlId: string, siteUrlId: string) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.page(crawlId, siteUrlId),
      queryFn: ({ signal }) => siteHealthApi.getPage(crawlId, siteUrlId, { signal }),
    }),
  issues: (crawlId: string, params?: IssuesParams) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.issues(crawlId, {
        cursor: params?.cursor ?? null,
        limit: params?.limit ?? null,
        severity: params?.severity ?? null,
        category: params?.category ?? null,
        dimension: params?.dimension ?? null,
        rule_id: params?.rule_id ?? null,
        site_url_id: params?.site_url_id ?? null,
      }),
      queryFn: ({ signal }) => siteHealthApi.getIssues(crawlId, params, { signal }),
    }),
  issue: (crawlId: string, issueId: string) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.issue(crawlId, issueId),
      queryFn: ({ signal }) => siteHealthApi.getIssue(crawlId, issueId, { signal }),
    }),
};

export const siteHealthMutations = {
  createCrawl: () =>
    mutationOptions({
      mutationFn: (input: CreateCrawlInput) => siteHealthApi.createCrawl(input),
    }),
  cancelCrawl: () =>
    mutationOptions({
      mutationFn: (crawlId: string) => siteHealthApi.cancelCrawl(crawlId),
    }),
  replaceMonitoredUrls: () =>
    mutationOptions({
      mutationFn: (vars: { projectId: string; input: ReplaceMonitoredInput }) =>
        siteHealthApi.replaceMonitoredUrls(vars.projectId, vars.input),
    }),
  rerunPage: () =>
    mutationOptions({
      mutationFn: (vars: { crawlId: string; siteUrlId: string }) =>
        siteHealthApi.rerunPage(vars.crawlId, vars.siteUrlId),
    }),
};
