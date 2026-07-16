/**
 * zod data contracts (F2) — the frontend's single source of truth for the
 * shape of every backend response it consumes.
 *
 * Contract invariants (docs/frontend-architecture.md §6/§7):
 *   - **Every `id` and `*_id` field is `z.string().uuid()`.** No numeric ids.
 *   - **No `user_id` anywhere** — the contract is workspace-scoped.
 *   - Provider secrets are **never** present on the wire (BYOK, invariant 6).
 *   - `sentiment` / `avg_position` are nullable at MVP (not computed; roadmap).
 *   - Validation **fails loud** via `strictValidate` — a mismatch is a bug to
 *     fix in the schema (backend is source of truth), never to swallow.
 */
import { z } from 'zod';

/** UUID id helper — all ids and foreign keys use this. */
const uuid = () => z.string().uuid();

// ---------------------------------------------------------------------------
// Auth / workspace
// ---------------------------------------------------------------------------

// Backend `SessionUser.role` is the ACCOUNT-level `User.role` (free-form
// string, defaults to `"user"` — see backend/app/models/user.py). It is a
// different axis from the per-workspace MEMBERSHIP role (`owner`/`member`,
// carried on `workspaceSchema.role` below) and must not be conflated with it
// via a restrictive enum — doing so previously rejected every real register/
// login response (`role: "user"` is not `owner|admin|member|viewer`).
export const sessionUserSchema = z
  .object({
    id: uuid(),
    email: z.string().email(),
    role: z.string(),
    is_active: z.boolean(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();

// register/login/me all return the authenticated user wrapped as
// `{ user: SessionUser }` (backend `AuthResponse`); the JWT rides the HttpOnly
// cookie, never the body. Fail loud on any extra key.
export const authResponseSchema = z.object({ user: sessionUserSchema }).strict();

// Backend `WorkspaceResponse` is `{ id, name, role, created_at, updated_at }` —
// no slug; the caller's membership `role` is carried instead.
export const workspaceSchema = z
  .object({
    id: uuid(),
    name: z.string(),
    role: z.string(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();

// ---------------------------------------------------------------------------
// Brand / project / prompts
// ---------------------------------------------------------------------------

export const competitorSchema = z
  .object({
    id: uuid(),
    name: z.string(),
    aliases: z.array(z.string()),
    domains: z.array(z.string()),
  })
  .strict();

// Intent enum. The B3 backend `normalize_intent` casefolds a free-text intent
// and normalizes any empty/unknown value to `''` ("unspecified"), so `''` is a
// valid on-the-wire value and must be accepted here (contract, not UI sugar).
export const promptIntentSchema = z.enum([
  '',
  'discovery',
  'comparison',
  'purchase',
  'service',
  'local',
]);

// Backend `PromptResponse.theme` is a non-null string (empty when unset), so
// the wire value is always a string — never null.
export const promptSchema = z
  .object({
    id: uuid(),
    prompt_set_id: uuid(),
    text: z.string(),
    theme: z.string(),
    intent: promptIntentSchema,
    branded: z.boolean(),
    enabled: z.boolean(),
    origin: z.enum(['manual', 'imported', 'generated']),
    // Evidence for a future AI-generated prompt (B-4 roadmap); null at MVP.
    generation_evidence: z.record(z.string(), z.unknown()).nullable().optional(),
    created_at: z.string().optional(),
    updated_at: z.string().optional(),
  })
  .strict();

export const promptSetSchema = z
  .object({
    id: uuid(),
    project_id: uuid(),
    name: z.string(),
    // B3 PromptSetResponse carries a description and a denormalized prompt_count.
    description: z.string().optional(),
    prompt_count: z.number().int().optional(),
    prompts: z.array(promptSchema),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();

export const benchmarkModeSchema = z.enum([
  'consumer_like',
  'controlled_localized',
  'forced_grounded',
]);

export const projectSchema = z
  .object({
    id: uuid(),
    workspace_id: uuid(),
    name: z.string(),
    brand_name: z.string(),
    website_url: z.string(),
    country_code: z.string(),
    language_code: z.string(),
    benchmark_mode: benchmarkModeSchema,
    default_repetitions: z.number().int(),
    brand: z
      .object({
        aliases: z.array(z.string()),
      })
      .strict(),
    owned_domains: z.array(z.string()),
    unintended_domains: z.array(z.string()),
    competitors: z.array(competitorSchema),
    prompt_sets: z.array(promptSetSchema),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();

// ---------------------------------------------------------------------------
// Providers (BYOK) — secret never present
// ---------------------------------------------------------------------------

// Active BYOK transports a connection may declare on the create/write path and
// that the provider catalog lists (v2 direct-provider retirement). Backend
// `ActiveTransportProvider` (providers/schemas) + `ACTIVE_TRANSPORTS`
// (provider_catalog): direct OpenAI / Anthropic / Google only. The retired
// `openrouter` token is NOT accepted here, so no new OpenRouter connection or
// route can be created.
export const transportProviderSchema = z.enum(['openai', 'anthropic', 'google']);
// Historical transport space including the retired `openrouter`. Read-only DTOs
// (provider connections/routes) accept it so legacy OpenRouter rows still parse
// under strict validation (invariant 10); it is never used for a write DTO.
export const historicalTransportProviderSchema = z.enum([
  'openai',
  'anthropic',
  'google',
  'openrouter',
]);
export const logicalEngineSchema = z.enum(['chatgpt', 'gemini', 'claude']);

// A configured route on a connection: which logical engine this transport
// serves and the concrete transport model to call. Reads accept the historical
// transport space so a legacy `openrouter` route still parses; `active` is
// false for retired routes so read clients can identify (and skip) them without
// seeing the internal deactivation marker.
export const providerRouteSchema = z
  .object({
    id: uuid(),
    logical_engine: logicalEngineSchema,
    transport_provider: historicalTransportProviderSchema,
    transport_model: z.string(),
    is_default: z.boolean(),
    // Backend defaults to true; legacy openrouter routes are returned false.
    active: z.boolean().optional(),
  })
  .strict();

export const providerConnectionSchema = z
  .object({
    id: uuid(),
    workspace_id: uuid(),
    // Optional so the pre-B4 minimal shape (used in the schema test) still
    // validates; the live B4 DTO always sends these.
    label: z.string().nullable().optional(),
    // Reads accept the historical transport space so a legacy `openrouter`
    // connection still parses under strict validation (invariant 10).
    transport_provider: historicalTransportProviderSchema,
    base_url: z.string().nullable(),
    active: z.boolean(),
    // Presence flag only — the key value itself is NEVER on the wire.
    api_key_set: z.boolean().optional(),
    last_tested_at: z.string().nullable().optional(),
    // Backend defaults to '' (untested); accept any short status string.
    last_test_status: z.string().optional(),
    routes: z.array(providerRouteSchema).optional(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  // Strict: an unexpected key (e.g. a leaked `api_key`/`secret`) is a contract
  // violation and must fail loud — the secret is never present on the wire.
  .strict();

export const providerCatalogRouteSchema = z
  .object({
    transport_provider: transportProviderSchema,
    default_model: z.string(),
  })
  .strict();

export const providerCatalogEngineSchema = z
  .object({
    logical_engine: logicalEngineSchema,
    routes: z.array(providerCatalogRouteSchema),
  })
  .strict();

export const providerCatalogSchema = z
  .object({
    transports: z.array(transportProviderSchema),
    engines: z.array(providerCatalogEngineSchema),
  })
  .strict();

// ---------------------------------------------------------------------------
// Audits (runs) + executions + evidence
// ---------------------------------------------------------------------------

export const auditStatusSchema = z.enum([
  'draft',
  'validating',
  'queued',
  'running',
  'analyzing',
  'reporting',
  'completed',
  'partially_completed',
  'failed',
  'cancelled',
]);

// The engine provenance a run froze at launch (B5 `AuditEngineSnapshotResponse`).
export const auditEngineSnapshotSchema = z
  .object({
    logical_engine: z.string(),
    transport_provider: z.string(),
    transport_model: z.string(),
  })
  .strict();

// A run/audit projection (B5 `AuditResponse`). `random_seed` is a decimal
// STRING (64-bit seed), `error_message` a non-null string ('' when unset), and
// the engine provenance is carried but the provider key never is (invariant 6).
export const auditSchema = z
  .object({
    id: uuid(),
    workspace_id: uuid(),
    project_id: uuid(),
    status: auditStatusSchema,
    benchmark_mode: z.string(),
    repetitions: z.number().int(),
    random_seed: z.string(),
    requested_count: z.number().int(),
    completed_count: z.number().int(),
    failed_count: z.number().int(),
    error_message: z.string(),
    engine_snapshots: z.array(auditEngineSnapshotSchema),
    created_at: z.string(),
    updated_at: z.string(),
    started_at: z.string().nullable(),
    completed_at: z.string().nullable(),
  })
  .strict();

// Deterministic citation classification (B6 `_classification`, invariant 4):
// owned / unintended (owned-but-unwanted) / competitor / third-party.
export const citationClassificationSchema = z.enum([
  'owned',
  'unintended',
  'competitor',
  'third_party',
]);

// One classified source citation on the evidence card (B6 `CitationEvidence`).
export const citationSchema = z
  .object({
    ordinal: z.number().int(),
    url: z.string(),
    title: z.string(),
    domain: z.string(),
    classification: citationClassificationSchema,
    is_owned: z.boolean(),
    is_unintended: z.boolean(),
    matched_competitor: z.string().nullable(),
  })
  .strict();

// Queue/execution row status (B5 task statuses).
export const executionStatusSchema = z.enum([
  'queued',
  'leased',
  'running',
  'succeeded',
  'retry_wait',
  'failed',
  'cancelled',
]);

// One execution/queue row in the run's executions table (B5 `AuditTaskResponse`).
// `answer_text` / `error_detail` default to '' (never null); the classified
// citation evidence lives on the single-execution evidence endpoint below.
export const executionSchema = z
  .object({
    id: uuid(),
    audit_id: uuid(),
    prompt_index: z.number().int(),
    repetition: z.number().int(),
    randomized_position: z.number().int(),
    logical_engine: z.string(),
    transport_provider: z.string(),
    transport_model: z.string(),
    status: executionStatusSchema,
    attempt_count: z.number().int(),
    max_attempts: z.number().int(),
    answer_text: z.string(),
    search_used: z.boolean(),
    error_code: z.string(),
    error_detail: z.string(),
    latency_ms: z.number().nullable(),
    created_at: z.string(),
    completed_at: z.string().nullable(),
  })
  .strict();

// One execution's persisted analysis + evidence (B6 `ExecutionEvidenceResponse`,
// `GET /executions/{id}`). `id`/`task_id` are the EXECUTION (AuditTask) id — the
// same id space as the executions list — so the evidence page keys off the row
// id. `analysis_id` is the internal ResponseAnalysis id (traceability only).
// `sentiment` / `avg_position` are present but null until the roadmap (B-2).
export const executionEvidenceSchema = z
  .object({
    id: uuid(),
    analysis_id: uuid(),
    audit_id: uuid(),
    task_id: uuid(),
    artifact_id: uuid().nullable(),
    analyzer_version: z.string(),
    scoring_rule_version: z.string(),
    logical_engine: z.string(),
    transport_provider: z.string(),
    transport_model: z.string(),
    prompt_index: z.number().int(),
    repetition: z.number().int(),
    prompt_class: z.string(),
    brand_mentioned: z.boolean(),
    brand_first_offset: z.number().int().nullable(),
    owned_domain_cited: z.boolean(),
    owned_citation_count: z.number().int(),
    unintended_domain_cited: z.boolean(),
    citation_count: z.number().int(),
    search_used: z.boolean(),
    search_query_count: z.number().int(),
    sentiment: z.string().nullable(),
    avg_position: z.number().nullable(),
    score: z.record(z.string(), z.unknown()).nullable(),
    citations: z.array(citationSchema),
    competitors_mentioned: z.array(z.string()),
    created_at: z.string(),
  })
  .strict();

// ---------------------------------------------------------------------------
// Visibility dashboard (selected-run projection)
// ---------------------------------------------------------------------------

// One per-engine comparison row for the selected run (B6 `EngineComparisonRow`).
export const visibilityEngineSchema = z
  .object({
    logical_engine: z.string(),
    total_completed: z.number().int(),
    brand_mention_rate: z.number().nullable(),
    owned_citation_rate: z.number().nullable(),
    search_use_rate: z.number().nullable(),
    visibility_score: z.number().nullable(),
  })
  .strict();

// One brand-vs-competitor rankings-table row (B6 `RankingRow`). `mention_rate`
// is the Visibility% and `share_of_voice` the SOV%; `sentiment` / `avg_position`
// are present but null until the roadmap computes them (decision B-2).
export const rankingRowSchema = z
  .object({
    name: z.string(),
    is_brand: z.boolean(),
    mention_rate: z.number().nullable(),
    citation_rate: z.number().nullable(),
    share_of_voice: z.number().nullable(),
    mention_count: z.number().int(),
    sentiment: z.string().nullable(),
    avg_position: z.number().nullable(),
  })
  .strict();

// Selected-run dashboard projection (B6 `VisibilityResponse`). Computed
// server-side from the persisted MetricSnapshot for the selected audit
// (defaults to the latest completed audit). No cross-run trend at MVP.
export const visibilitySchema = z
  .object({
    project_id: uuid(),
    audit_id: uuid(),
    audit_status: auditStatusSchema,
    analyzer_version: z.string(),
    scoring_rule_version: z.string(),
    total_completed: z.number().int(),
    total_failed: z.number().int(),
    visibility_score: z.number(),
    rankings: z.array(rankingRowSchema),
    per_engine: z.array(visibilityEngineSchema),
    sentiment: z.string().nullable(),
    avg_position: z.number().nullable(),
    created_at: z.string(),
  })
  .strict();

// ---------------------------------------------------------------------------
// Cross-run Visibility trend history (projection over persisted snapshots)
// ---------------------------------------------------------------------------

// Both Share-of-Voice definitions for one trend point (B backend
// `VisibilityTrendSov`). `response` is the response-level SOV (brand
// response-presence share vs competitors); `mention` is the mention-level SOV
// derived from the persisted `share_of_voice.mention_counts`. Both are
// deterministic reprojections of persisted metrics (invariant 7) and are
// nullable when the source metric is absent.
export const visibilityTrendSovSchema = z
  .object({
    response: z.number().nullable(),
    mention: z.number().nullable(),
  })
  .strict();

// One brand-vs-competitor ranking-history row within a trend point (backend
// `VisibilityTrendRankingRow`). `sentiment` / `avg_position` are present but
// null until an LLM stage is added (decision B-2 / invariant 9).
export const visibilityTrendRankingRowSchema = z
  .object({
    name: z.string(),
    is_brand: z.boolean(),
    mention_rate: z.number().nullable(),
    citation_rate: z.number().nullable(),
    share_of_voice: z.number().nullable(),
    mention_count: z.number().int(),
    sentiment: z.string().nullable(),
    avg_position: z.number().nullable(),
  })
  .strict();

// One point in the cross-run Visibility trend (backend `VisibilityTrendPoint`).
// A raw per-run point carries a set `audit_id`; a week/month bucket folds many
// snapshots (`audit_id` is null) and carries the full provenance list. Version
// metadata lists every distinct analyzer/scoring version the point folds, with
// `spans_version_boundary` set when a bucket mixes versions. `sentiment` /
// `avg_position` stay null (decision B-2 / invariant 9).
export const visibilityTrendPointSchema = z
  .object({
    audit_id: uuid().nullable(),
    completed_at: z.string(),
    logical_engine: z.string().nullable(),
    visibility_score: z.number().nullable(),
    brand_mention_rate: z.number().nullable(),
    owned_citation_rate: z.number().nullable(),
    sov: visibilityTrendSovSchema,
    rankings: z.array(visibilityTrendRankingRowSchema),
    sentiment: z.string().nullable(),
    avg_position: z.number().nullable(),
    // Provenance (invariant 4): every source snapshot this point folds.
    source_snapshot_ids: z.array(uuid()),
    // Distinct versions across the folded snapshots (invariant 4).
    analyzer_versions: z.array(z.string()),
    scoring_rule_versions: z.array(z.string()),
    spans_version_boundary: z.boolean(),
  })
  .strict();

// The trends endpoint returns a chronological list of points (never wrapped).
export const visibilityTrendListSchema = z.array(visibilityTrendPointSchema);

// ---------------------------------------------------------------------------
// Execution-evidence projection (Mentions & Citations + Query Fanout tabs)
// `GET /projects/{id}/visibility/evidence`. A pure read projection over already
// persisted mention/citation/task/artifact rows — nothing is inferred or
// backfilled at read time (invariant 7). Transport/model stay plain strings so
// a legacy (e.g. `openrouter`) row still parses under strict validation.
// ---------------------------------------------------------------------------

// Three-state query-fanout availability for one execution (backend
// `VisibilityFanoutState`): `queries_available` (≥1 stored event has non-blank
// query text), `count_only` (search used / count positive but no query text —
// e.g. a legacy count-only row), `no_search` (neither signal present).
export const visibilityFanoutStateSchema = z.enum([
  'queries_available',
  'count_only',
  'no_search',
]);

// One normalized stored search event (backend `VisibilityEvidenceSearchEvent`).
// Empty query strings are preserved verbatim (a count-only event); query text
// is never invented.
export const visibilityEvidenceSearchEventSchema = z
  .object({
    sequence: z.number().int(),
    query: z.string(),
    call_id: z.string(),
    call_sequence: z.number().int(),
    query_sequence: z.number().int(),
  })
  .strict();

// One persisted brand/competitor mention row (backend
// `VisibilityMentionEvidence`). Projected directly from `BrandMention` /
// `CompetitorMention`; never inferred from answer text at read time.
export const visibilityMentionEvidenceSchema = z
  .object({
    kind: z.enum(['brand', 'competitor']),
    name: z.string(),
    first_offset: z.number().int().nullable(),
    artifact_id: uuid().nullable(),
    analyzer_version: z.string(),
  })
  .strict();

// One execution's persisted mention/citation + query-fanout evidence (backend
// `VisibilityExecutionEvidence`). `prompt_id` is nullable so a deleted source
// prompt stays readable via its frozen `prompt_text`; `completed_at` is
// nullable for an incomplete/legacy row.
export const visibilityExecutionEvidenceSchema = z
  .object({
    audit_id: uuid(),
    task_id: uuid(),
    analysis_id: uuid(),
    artifact_id: uuid().nullable(),
    prompt_snapshot_id: uuid(),
    prompt_id: uuid().nullable(),
    prompt_index: z.number().int(),
    prompt_text: z.string(),
    repetition: z.number().int(),
    completed_at: z.string().nullable(),
    logical_engine: z.string(),
    transport_provider: z.string(),
    transport_model: z.string(),
    search_used: z.boolean(),
    search_query_count: z.number().int(),
    query_text_available: z.boolean(),
    state: visibilityFanoutStateSchema,
    search_events: z.array(visibilityEvidenceSearchEventSchema),
    event_source: z.enum(['raw_artifact', 'audit_task', 'none']),
    mentions: z.array(visibilityMentionEvidenceSchema),
    citations: z.array(citationSchema),
  })
  .strict();

// The shared evidence dataset for the two evidence tabs (backend
// `VisibilityEvidenceResponse`). `items` is newest-first; `truncated` is set
// when more than `limit` matches exist (no offset/cursor/total).
export const visibilityEvidenceResponseSchema = z
  .object({
    items: z.array(visibilityExecutionEvidenceSchema),
    truncated: z.boolean(),
  })
  .strict();

// ---------------------------------------------------------------------------
// strictValidate — fail loud on any schema drift (drift policy §6)
// ---------------------------------------------------------------------------

/**
 * Validate `data` against `schema`, throwing a descriptive error tagged with
 * `context` on any mismatch. The backend is the source of truth: a failure here
 * means `schemas.ts` is out of sync and must be fixed — never swallowed.
 */
export function strictValidate<T>(schema: z.ZodType<T>, data: unknown, context: string): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    throw new Error(`API validation failure in ${context}: ${result.error.message}`);
  }
  return result.data;
}
