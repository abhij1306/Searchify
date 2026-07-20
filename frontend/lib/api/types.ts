/**
 * Inferred contract types (F2).
 *
 * Every type is derived from a zod schema in `schemas.ts` via `z.infer`, so the
 * schema is the single source of truth and the two can never drift. All `id` /
 * `*_id` fields are string UUIDs; there is no numeric id and no `user_id`.
 */
import type { z } from 'zod';

import type {
  auditSchema,
  auditStatusSchema,
  authResponseSchema,
  benchmarkModeSchema,
  citationClassificationSchema,
  executionEvidenceSchema,
  executionSchema,
  executionStatusSchema,
  historicalTransportProviderSchema,
  logicalEngineSchema,
  projectSchema,
  promptGenerateResponseSchema,
  promptIntentSchema,
  promptSchema,
  promptSetSchema,
  promptStatusSchema,
  providerCatalogSchema,
  providerConnectionSchema,
  rankingRowSchema,
  sessionUserSchema,
  transportProviderSchema,
  topicSchema,
  visibilityEngineSchema,
  visibilityEvidenceResponseSchema,
  visibilityExecutionEvidenceSchema,
  visibilitySchema,
  visibilityTrendPointSchema,
  visibilityTrendRankingRowSchema,
  workspaceSchema,
  // Site Health
  crawlAnalysisStatusSchema,
  crawlDiscoveryStatusSchema,
  crawlOverallStatusSchema,
  deliveryFactsSchema,
  inventoryPageSchema,
  inventoryRowSchema,
  issueDimensionSchema,
  issueHistoryPageSchema,
  issueSeveritySchema,
  issuesSummarySchema,
  monitoredUrlSchema,
  monitoredUrlsResponseSchema,
  pageAnalysisStatusSchema,
  pageDetailSchema,
  pageSummarySchema,
  pagesPageSchema,
  rerunPageResponseSchema,
  siteCrawlListPageSchema,
  siteCrawlSchema,
  siteHealthDashboardSchema,
  siteHealthEntitlementSchema,
  siteIssueDetailSchema,
  siteIssueSchema,
  siteIssuesPageSchema,
  // Content
  contentGenerationDetailSchema,
  contentGenerationListItemSchema,
  contentGenerationStatusSchema,
  contentOutputTypeSchema,
  websiteContextStatusSchema,
  websiteContextSummarySchema,
} from './schemas';

export type SessionUser = z.infer<typeof sessionUserSchema>;
export type AuthResponse = z.infer<typeof authResponseSchema>;
export type Workspace = z.infer<typeof workspaceSchema>;
export type PromptIntent = z.infer<typeof promptIntentSchema>;
export type Prompt = z.infer<typeof promptSchema>;
export type PromptStatus = z.infer<typeof promptStatusSchema>;
export type PromptSet = z.infer<typeof promptSetSchema>;
export type Topic = z.infer<typeof topicSchema>;
export type PromptGenerateResponse = z.infer<typeof promptGenerateResponseSchema>;
export type BenchmarkMode = z.infer<typeof benchmarkModeSchema>;
export type Project = z.infer<typeof projectSchema>;
export type TransportProvider = z.infer<typeof transportProviderSchema>;
/** Historical transport space including the retired `openrouter` (read DTOs). */
export type HistoricalTransportProvider = z.infer<typeof historicalTransportProviderSchema>;
export type LogicalEngine = z.infer<typeof logicalEngineSchema>;
export type ProviderConnection = z.infer<typeof providerConnectionSchema>;
export type ProviderCatalog = z.infer<typeof providerCatalogSchema>;
export type AuditStatus = z.infer<typeof auditStatusSchema>;
export type Audit = z.infer<typeof auditSchema>;
export type ExecutionStatus = z.infer<typeof executionStatusSchema>;
export type CitationClassification = z.infer<typeof citationClassificationSchema>;
export type Execution = z.infer<typeof executionSchema>;
export type ExecutionEvidence = z.infer<typeof executionEvidenceSchema>;
export type VisibilityEngine = z.infer<typeof visibilityEngineSchema>;
export type RankingRow = z.infer<typeof rankingRowSchema>;
export type Visibility = z.infer<typeof visibilitySchema>;

// --- Site Health ---
export type SiteHealthEntitlement = z.infer<typeof siteHealthEntitlementSchema>;
export type CrawlOverallStatus = z.infer<typeof crawlOverallStatusSchema>;
export type CrawlDiscoveryStatus = z.infer<typeof crawlDiscoveryStatusSchema>;
export type CrawlAnalysisStatus = z.infer<typeof crawlAnalysisStatusSchema>;
export type PageAnalysisStatus = z.infer<typeof pageAnalysisStatusSchema>;
export type SiteCrawl = z.infer<typeof siteCrawlSchema>;
export type InventoryRow = z.infer<typeof inventoryRowSchema>;
export type InventoryPage = z.infer<typeof inventoryPageSchema>;
export type SiteCrawlListPage = z.infer<typeof siteCrawlListPageSchema>;
export type MonitoredUrl = z.infer<typeof monitoredUrlSchema>;
export type MonitoredUrlsResponse = z.infer<typeof monitoredUrlsResponseSchema>;
export type DeliveryFacts = z.infer<typeof deliveryFactsSchema>;
export type IssueSeverity = z.infer<typeof issueSeveritySchema>;
export type IssueDimension = z.infer<typeof issueDimensionSchema>;
export type SiteIssue = z.infer<typeof siteIssueSchema>;
export type SiteIssueDetail = z.infer<typeof siteIssueDetailSchema>;
export type SiteIssuesPage = z.infer<typeof siteIssuesPageSchema>;
export type IssuesSummary = z.infer<typeof issuesSummarySchema>;
export type IssueHistoryPage = z.infer<typeof issueHistoryPageSchema>;
export type PageSummary = z.infer<typeof pageSummarySchema>;
export type PagesPage = z.infer<typeof pagesPageSchema>;
export type PageDetail = z.infer<typeof pageDetailSchema>;
export type RerunPageResponse = z.infer<typeof rerunPageResponseSchema>;
export type SiteHealthDashboard = z.infer<typeof siteHealthDashboardSchema>;
export type VisibilityTrendRankingRow = z.infer<typeof visibilityTrendRankingRowSchema>;
export type VisibilityTrendPoint = z.infer<typeof visibilityTrendPointSchema>;
export type VisibilityExecutionEvidence = z.infer<typeof visibilityExecutionEvidenceSchema>;
export type VisibilityEvidenceResponse = z.infer<typeof visibilityEvidenceResponseSchema>;

// --- Content ---
export type ContentGenerationStatus = z.infer<typeof contentGenerationStatusSchema>;
export type ContentOutputType = z.infer<typeof contentOutputTypeSchema>;
export type WebsiteContextStatus = z.infer<typeof websiteContextStatusSchema>;
export type WebsiteContextSummary = z.infer<typeof websiteContextSummarySchema>;
export type ContentGenerationListItem = z.infer<typeof contentGenerationListItemSchema>;
export type ContentGenerationDetail = z.infer<typeof contentGenerationDetailSchema>;
