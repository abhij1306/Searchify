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
export type VisibilityTrendSov = z.infer<typeof visibilityTrendSovSchema>;
export type VisibilityTrendRankingRow = z.infer<typeof visibilityTrendRankingRowSchema>;
export type VisibilityTrendPoint = z.infer<typeof visibilityTrendPointSchema>;
export type VisibilityFanoutState = z.infer<typeof visibilityFanoutStateSchema>;
export type VisibilityEvidenceSearchEvent = z.infer<typeof visibilityEvidenceSearchEventSchema>;
export type VisibilityMentionEvidence = z.infer<typeof visibilityMentionEvidenceSchema>;
export type VisibilityExecutionEvidence = z.infer<typeof visibilityExecutionEvidenceSchema>;
export type VisibilityEvidenceResponse = z.infer<typeof visibilityEvidenceResponseSchema>;
