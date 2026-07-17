/**
 * Inferred contract types (F2).
 *
 * Every type is derived from a zod schema in `schemas.ts` via `z.infer`, so the
 * schema is the single source of truth and the two can never drift. All `id` /
 * `*_id` fields are string UUIDs; there is no numeric id and no `user_id`.
 */
import type { z } from 'zod';

import type {
  auditEngineSnapshotSchema,
  auditSchema,
  auditStatusSchema,
  authResponseSchema,
  benchmarkModeSchema,
  citationClassificationSchema,
  citationSchema,
  competitorSchema,
  executionEvidenceSchema,
  executionSchema,
  executionStatusSchema,
  historicalTransportProviderSchema,
  logicalEngineSchema,
  projectSchema,
  promptIntentSchema,
  promptSchema,
  promptSetSchema,
  providerCatalogSchema,
  providerConnectionSchema,
  providerRouteSchema,
  rankingRowSchema,
  sessionUserSchema,
  transportProviderSchema,
  visibilityEngineSchema,
  visibilityEvidenceResponseSchema,
  visibilityEvidenceSearchEventSchema,
  visibilityExecutionEvidenceSchema,
  visibilityFanoutStateSchema,
  visibilityMentionEvidenceSchema,
  visibilitySchema,
  visibilityTrendPointSchema,
  visibilityTrendRankingRowSchema,
  visibilityTrendSovSchema,
  workspaceSchema,
  // Site Health
  affectedUrlSchema,
  crawlAnalysisStatusSchema,
  crawlDiscoveryStatusSchema,
  crawlOverallStatusSchema,
  deliveryFactsSchema,
  inventoryPageSchema,
  inventoryRowSchema,
  issueDimensionSchema,
  issueHistoryPageSchema,
  issueHistoryRowSchema,
  issueSeveritySchema,
  issuesSummarySchema,
  monitoredQuotaSchema,
  monitoredUrlSchema,
  monitoredUrlsResponseSchema,
  pageAnalysisStatusSchema,
  pageDetailSchema,
  pageFactsSchema,
  pageSummarySchema,
  pagesPageSchema,
  siteCrawlEventSchema,
  siteCrawlListPageSchema,
  siteCrawlSchema,
  siteCrawlTaskStatusSchema,
  siteHealthAccessModeSchema,
  siteHealthDashboardSchema,
  siteHealthEntitlementSchema,
  siteHealthErrorCodeSchema,
  siteHealthErrorSchema,
  siteHealthPlanSchema,
  siteIssueDetailSchema,
  siteIssueSchema,
  siteIssuesPageSchema,
  siteScoreSummarySchema,
  siteUrlSourceSchema,
} from './schemas';

export type SessionUser = z.infer<typeof sessionUserSchema>;
export type AuthResponse = z.infer<typeof authResponseSchema>;
export type Workspace = z.infer<typeof workspaceSchema>;
export type Competitor = z.infer<typeof competitorSchema>;
export type PromptIntent = z.infer<typeof promptIntentSchema>;
export type Prompt = z.infer<typeof promptSchema>;
export type PromptSet = z.infer<typeof promptSetSchema>;
export type BenchmarkMode = z.infer<typeof benchmarkModeSchema>;
export type Project = z.infer<typeof projectSchema>;
export type TransportProvider = z.infer<typeof transportProviderSchema>;
/** Historical transport space including the retired `openrouter` (read DTOs). */
export type HistoricalTransportProvider = z.infer<typeof historicalTransportProviderSchema>;
export type LogicalEngine = z.infer<typeof logicalEngineSchema>;
export type ProviderConnection = z.infer<typeof providerConnectionSchema>;
export type ProviderRoute = z.infer<typeof providerRouteSchema>;
export type ProviderCatalog = z.infer<typeof providerCatalogSchema>;
export type AuditStatus = z.infer<typeof auditStatusSchema>;
export type AuditEngineSnapshot = z.infer<typeof auditEngineSnapshotSchema>;
export type Audit = z.infer<typeof auditSchema>;
export type ExecutionStatus = z.infer<typeof executionStatusSchema>;
export type CitationClassification = z.infer<typeof citationClassificationSchema>;
export type Citation = z.infer<typeof citationSchema>;
export type Execution = z.infer<typeof executionSchema>;
export type ExecutionEvidence = z.infer<typeof executionEvidenceSchema>;
export type VisibilityEngine = z.infer<typeof visibilityEngineSchema>;
export type RankingRow = z.infer<typeof rankingRowSchema>;
export type Visibility = z.infer<typeof visibilitySchema>;

// --- Site Health ---
export type SiteHealthPlan = z.infer<typeof siteHealthPlanSchema>;
export type SiteHealthAccessMode = z.infer<typeof siteHealthAccessModeSchema>;
export type SiteHealthEntitlement = z.infer<typeof siteHealthEntitlementSchema>;
export type CrawlOverallStatus = z.infer<typeof crawlOverallStatusSchema>;
export type CrawlDiscoveryStatus = z.infer<typeof crawlDiscoveryStatusSchema>;
export type CrawlAnalysisStatus = z.infer<typeof crawlAnalysisStatusSchema>;
export type SiteCrawlTaskStatus = z.infer<typeof siteCrawlTaskStatusSchema>;
export type SiteUrlSource = z.infer<typeof siteUrlSourceSchema>;
export type PageAnalysisStatus = z.infer<typeof pageAnalysisStatusSchema>;
export type SiteScoreSummary = z.infer<typeof siteScoreSummarySchema>;
export type SiteCrawl = z.infer<typeof siteCrawlSchema>;
export type InventoryRow = z.infer<typeof inventoryRowSchema>;
export type InventoryPage = z.infer<typeof inventoryPageSchema>;
export type SiteCrawlListPage = z.infer<typeof siteCrawlListPageSchema>;
export type MonitoredQuota = z.infer<typeof monitoredQuotaSchema>;
export type MonitoredUrl = z.infer<typeof monitoredUrlSchema>;
export type MonitoredUrlsResponse = z.infer<typeof monitoredUrlsResponseSchema>;
export type DeliveryFacts = z.infer<typeof deliveryFactsSchema>;
export type PageFacts = z.infer<typeof pageFactsSchema>;
export type IssueSeverity = z.infer<typeof issueSeveritySchema>;
export type IssueDimension = z.infer<typeof issueDimensionSchema>;
export type AffectedUrl = z.infer<typeof affectedUrlSchema>;
export type SiteIssue = z.infer<typeof siteIssueSchema>;
export type SiteIssueDetail = z.infer<typeof siteIssueDetailSchema>;
export type SiteIssuesPage = z.infer<typeof siteIssuesPageSchema>;
export type IssuesSummary = z.infer<typeof issuesSummarySchema>;
export type IssueHistoryRow = z.infer<typeof issueHistoryRowSchema>;
export type IssueHistoryPage = z.infer<typeof issueHistoryPageSchema>;
export type PageSummary = z.infer<typeof pageSummarySchema>;
export type PagesPage = z.infer<typeof pagesPageSchema>;
export type PageDetail = z.infer<typeof pageDetailSchema>;
export type SiteCrawlEvent = z.infer<typeof siteCrawlEventSchema>;
export type SiteHealthDashboard = z.infer<typeof siteHealthDashboardSchema>;
export type SiteHealthErrorCode = z.infer<typeof siteHealthErrorCodeSchema>;
export type SiteHealthError = z.infer<typeof siteHealthErrorSchema>;
export type VisibilityTrendSov = z.infer<typeof visibilityTrendSovSchema>;
export type VisibilityTrendRankingRow = z.infer<typeof visibilityTrendRankingRowSchema>;
export type VisibilityTrendPoint = z.infer<typeof visibilityTrendPointSchema>;
export type VisibilityFanoutState = z.infer<typeof visibilityFanoutStateSchema>;
export type VisibilityEvidenceSearchEvent = z.infer<typeof visibilityEvidenceSearchEventSchema>;
export type VisibilityMentionEvidence = z.infer<typeof visibilityMentionEvidenceSchema>;
export type VisibilityExecutionEvidence = z.infer<typeof visibilityExecutionEvidenceSchema>;
export type VisibilityEvidenceResponse = z.infer<typeof visibilityEvidenceResponseSchema>;
