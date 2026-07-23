/**
 * LLM Analytics domain endpoints (F3): the headline AEO Insights projection,
 * the paged classified-referrals drill-down, and the theme rollup.
 *
 * Owns transport for the analytics slice. Read endpoints render persisted
 * projections only (invariant 7 — no recomputation); every metric is
 * deterministic (no LLM, invariant 9). A correlation below the minimum
 * sample size arrives as `insufficient_data` with a null coefficient —
 * never a fabricated number; the UI renders `—`. Every JSON response passes
 * through `strictValidate` (fail loud on any drift). All paths are relative
 * `/api/v1` (same-origin proxy, invariant 12).
 */
import type { z } from 'zod';

import { apiClient, type ApiRequestOptions } from './client';
import {
  analyticsReferralsPageSchema,
  llmAnalyticsSchema,
  llmAnalyticsThemeListSchema,
  strictValidate,
  type aiSourceSchema,
  type snapshotGranularitySchema,
} from './schemas';
import { definedQuery, withQuery } from './shared';

type SnapshotGranularity = z.infer<typeof snapshotGranularitySchema>;

export type AiSource = z.infer<typeof aiSourceSchema>;
export type LlmAnalytics = z.infer<typeof llmAnalyticsSchema>;
export type AnalyticsCorrelation = LlmAnalytics['correlation'];
export type AnalyticsReferralRow = z.infer<typeof analyticsReferralsPageSchema>['items'][number];
export type AnalyticsReferralsPage = z.infer<typeof analyticsReferralsPageSchema>;
export type LlmAnalyticsThemeRow = z.infer<typeof llmAnalyticsThemeListSchema>[number];

/** Headline window query (`from`/`to` ISO dates, snapshot granularity). */
export type AnalyticsWindowParams = {
  from?: string;
  to?: string;
  granularity?: SnapshotGranularity;
};

/** Keyset referrals drill-down query (C4) with an optional source filter. */
export type AnalyticsReferralsParams = {
  source?: AiSource;
  from?: string;
  to?: string;
  cursor?: string;
};

/** Theme rollup window query. */
export type AnalyticsThemesParams = {
  from?: string;
  to?: string;
};

export const analyticsApi = {
  getAnalytics: async (
    projectId: string,
    params?: AnalyticsWindowParams,
    options?: ApiRequestOptions,
  ) => {
    const path = withQuery(`/projects/${projectId}/llm-analytics`, definedQuery(params));
    const res = await apiClient.get<LlmAnalytics>(path, options);
    return strictValidate(llmAnalyticsSchema, res, 'analytics.getAnalytics');
  },
  getReferrals: async (
    projectId: string,
    params?: AnalyticsReferralsParams,
    options?: ApiRequestOptions,
  ) => {
    const path = withQuery(`/projects/${projectId}/llm-analytics/referrals`, definedQuery(params));
    const res = await apiClient.get<AnalyticsReferralsPage>(path, options);
    return strictValidate(analyticsReferralsPageSchema, res, 'analytics.getReferrals');
  },
  getThemes: async (
    projectId: string,
    params?: AnalyticsThemesParams,
    options?: ApiRequestOptions,
  ) => {
    const path = withQuery(`/projects/${projectId}/llm-analytics/themes`, definedQuery(params));
    const res = await apiClient.get<LlmAnalyticsThemeRow[]>(path, options);
    return strictValidate(llmAnalyticsThemeListSchema, res, 'analytics.getThemes');
  },
};
