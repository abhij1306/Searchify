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

export const sessionUserSchema = z
  .object({
    id: uuid(),
    email: z.string().email(),
    role: z.enum(['owner', 'admin', 'member', 'viewer']),
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

// MVP transports a BYOK connection may declare and that appear on the wire in
// create/update/response DTOs. Backend `TransportProvider` (providers/schemas)
// + `MVP_TRANSPORTS` (provider_catalog) exclude direct `openai` at MVP.
export const transportProviderSchema = z.enum(['anthropic', 'google', 'openrouter']);
// Wider UI-only transport space, including the reserved/disabled `openai`
// route the F8 providers UI renders as "coming soon". Never used to validate an
// API request/response DTO — only for the static reserved-route options.
export const uiTransportProviderSchema = z.enum(['anthropic', 'google', 'openrouter', 'openai']);
export const logicalEngineSchema = z.enum(['chatgpt', 'gemini', 'claude']);

// A configured route on a connection: which logical engine this transport
// serves and the concrete transport model to call.
export const providerRouteSchema = z
  .object({
    id: uuid(),
    logical_engine: logicalEngineSchema,
    transport_provider: transportProviderSchema,
    transport_model: z.string(),
    is_default: z.boolean(),
  })
  .strict();

export const providerConnectionSchema = z
  .object({
    id: uuid(),
    workspace_id: uuid(),
    // Optional so the pre-B4 minimal shape (used in the schema test) still
    // validates; the live B4 DTO always sends these.
    label: z.string().nullable().optional(),
    transport_provider: transportProviderSchema,
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
