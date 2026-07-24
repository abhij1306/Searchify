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

import { API_BASE_URL, apiClient, getActiveWorkspaceId, type ApiRequestOptions } from './client';
import { queryKeys } from './query-keys';
import {
  inventoryPageSchema,
  issueHistoryPageSchema,
  monitoredUrlsResponseSchema,
  pageDetailSchema,
  pagesPageSchema,
  rerunPageResponseSchema,
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
  IssueHistoryPage,
  MonitoredUrlsResponse,
  PageDetail,
  PagesPage,
  RerunPageResponse,
  SiteCrawl,
  SiteCrawlListPage,
  SiteHealthDashboard,
  SiteHealthEntitlement,
  SiteIssueDetail,
  SiteIssuesPage,
} from './types';

/** `POST /site-crawls` body. Workspace is resolved from `X-Workspace-Id`. */
type CreateCrawlInput = {
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
  /** v2 P1: filter to one classified page type (omitted = all types). */
  page_type?: string;
};

type CrawlListParams = { project_id: string; limit?: number; cursor?: string };
export type PagesParams = {
  cursor?: string;
  limit?: number;
  status?: string;
  monitored?: boolean;
  /** v2 P1: filter to one classified page type (omitted = all types). */
  page_type?: string;
};
export type IssuesParams = {
  cursor?: string;
  limit?: number;
  query?: string;
  severity?: string;
  category?: string;
  dimension?: string;
  rule?: string;
  site_url_id?: string;
  /** v2 P1: filter to issues affecting one classified page type. */
  page_type?: string;
};

/** Keyset params for a grouped issue's affected-URL page. */
type IssueDetailParams = { cursor?: string; limit?: number };

/** Keyset params for a URL's crawl-bounded issue history. */
type IssueHistoryParams = { cursor?: string; limit?: number };

/** `PUT /projects/{id}/monitored-urls` body — atomic full-set replacement. */
type ReplaceMonitoredInput = {
  site_url_ids: string[];
  expected_selection_version: number;
};

/**
 * `POST /projects/{id}/monitored-urls/bulk-select` body — server-resolved
 * bulk selection. `first_n` selects the first `count` admitted URLs of
 * `crawl_id` in the inventory's `(normalized_url, id)` order; `all` selects
 * every admitted URL; `none` clears the selection. `query` applies the same
 * substring filter as the inventory listing.
 */
export type BulkSelectMonitoredInput = {
  mode: 'first_n' | 'all' | 'none';
  crawl_id: string;
  count?: number;
  query?: string;
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
    const res = await apiClient.post<SiteCrawl>(
      `/site-crawls/${crawlId}/cancel`,
      undefined,
      options,
    );
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
  bulkSelectMonitoredUrls: async (
    projectId: string,
    input: BulkSelectMonitoredInput,
    options?: ApiRequestOptions,
  ) => {
    const res = await apiClient.post<MonitoredUrlsResponse>(
      `/projects/${projectId}/monitored-urls/bulk-select`,
      input,
      options,
    );
    return strictValidate(monitoredUrlsResponseSchema, res, 'siteHealth.bulkSelectMonitoredUrls');
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
    // 202 Accepted carrying the (possibly fresh) rerun identity + status so
    // the client polls the new run — not the terminal source crawl.
    const res = await apiClient.post<RerunPageResponse>(
      `/site-crawls/${crawlId}/pages/${siteUrlId}/rerun`,
      undefined,
      options,
    );
    return strictValidate(rerunPageResponseSchema, res, 'siteHealth.rerunPage');
  },
  getIssues: async (crawlId: string, params?: IssuesParams, options?: ApiRequestOptions) => {
    const path = withQuery(`/site-crawls/${crawlId}/issues`, definedQuery(params));
    const res = await apiClient.get<SiteIssuesPage>(path, options);
    return strictValidate(siteIssuesPageSchema, res, 'siteHealth.getIssues');
  },
  getIssue: async (
    crawlId: string,
    issueId: string,
    params?: IssueDetailParams,
    options?: ApiRequestOptions,
  ) => {
    const path = withQuery(`/site-crawls/${crawlId}/issues/${issueId}`, definedQuery(params));
    const res = await apiClient.get<SiteIssueDetail>(path, options);
    return strictValidate(siteIssueDetailSchema, res, 'siteHealth.getIssue');
  },
  getIssueHistory: async (
    crawlId: string,
    siteUrlId: string,
    params?: IssueHistoryParams,
    options?: ApiRequestOptions,
  ) => {
    const path = withQuery(
      `/site-crawls/${crawlId}/pages/${siteUrlId}/issue-history`,
      definedQuery(params),
    );
    const res = await apiClient.get<IssueHistoryPage>(path, options);
    return strictValidate(issueHistoryPageSchema, res, 'siteHealth.getIssueHistory');
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
      queryKey: queryKeys.siteHealth.entitlements(getActiveWorkspaceId()),
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
        page_type: params?.page_type ?? null,
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
        page_type: params?.page_type ?? null,
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
        query: params?.query ?? null,
        severity: params?.severity ?? null,
        category: params?.category ?? null,
        dimension: params?.dimension ?? null,
        rule: params?.rule ?? null,
        site_url_id: params?.site_url_id ?? null,
        page_type: params?.page_type ?? null,
      }),
      queryFn: ({ signal }) => siteHealthApi.getIssues(crawlId, params, { signal }),
    }),
  issue: (crawlId: string, issueId: string, params?: IssueDetailParams) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.issue(crawlId, issueId, {
        cursor: params?.cursor ?? null,
        limit: params?.limit ?? null,
      }),
      queryFn: ({ signal }) => siteHealthApi.getIssue(crawlId, issueId, params, { signal }),
    }),
  issueHistory: (crawlId: string, siteUrlId: string, params?: IssueHistoryParams) =>
    queryOptions({
      queryKey: queryKeys.siteHealth.issueHistory(crawlId, siteUrlId, {
        cursor: params?.cursor ?? null,
        limit: params?.limit ?? null,
      }),
      queryFn: ({ signal }) =>
        siteHealthApi.getIssueHistory(crawlId, siteUrlId, params, { signal }),
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
  bulkSelectMonitoredUrls: () =>
    mutationOptions({
      mutationFn: (vars: { projectId: string; input: BulkSelectMonitoredInput }) =>
        siteHealthApi.bulkSelectMonitoredUrls(vars.projectId, vars.input),
    }),
  rerunPage: () =>
    mutationOptions({
      mutationFn: (vars: { crawlId: string; siteUrlId: string }) =>
        siteHealthApi.rerunPage(vars.crawlId, vars.siteUrlId),
    }),
};
