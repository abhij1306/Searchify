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

export const sessionUserSchema = z.object({
  id: uuid(),
  email: z.string().email(),
  role: z.enum(['owner', 'admin', 'member', 'viewer']),
  is_active: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const workspaceSchema = z.object({
  id: uuid(),
  name: z.string(),
  slug: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
});

// ---------------------------------------------------------------------------
// Brand / project / prompts
// ---------------------------------------------------------------------------

export const competitorSchema = z.object({
  id: uuid(),
  name: z.string(),
  aliases: z.array(z.string()),
  domains: z.array(z.string()),
});

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

// Backend `theme` is a non-null string (empty when unset); keep it nullable so
// an explicit null never fails, but `''` is the common case.
export const promptSchema = z.object({
  id: uuid(),
  prompt_set_id: uuid(),
  text: z.string(),
  theme: z.string().nullable(),
  intent: promptIntentSchema,
  branded: z.boolean(),
  enabled: z.boolean(),
  origin: z.enum(['manual', 'imported', 'generated']),
  // Evidence for a future AI-generated prompt (B-4 roadmap); null at MVP.
  generation_evidence: z.record(z.string(), z.unknown()).nullable().optional(),
  created_at: z.string().optional(),
  updated_at: z.string().optional(),
});

export const promptSetSchema = z.object({
  id: uuid(),
  project_id: uuid(),
  name: z.string(),
  // B3 PromptSetResponse carries a description and a denormalized prompt_count.
  description: z.string().optional(),
  prompt_count: z.number().int().optional(),
  prompts: z.array(promptSchema),
  created_at: z.string(),
  updated_at: z.string(),
});

export const benchmarkModeSchema = z.enum([
  'consumer_like',
  'controlled_localized',
  'forced_grounded',
]);

export const projectSchema = z.object({
  id: uuid(),
  workspace_id: uuid(),
  name: z.string(),
  brand_name: z.string(),
  website_url: z.string(),
  country_code: z.string(),
  language_code: z.string(),
  benchmark_mode: benchmarkModeSchema,
  default_repetitions: z.number().int(),
  brand: z.object({
    aliases: z.array(z.string()),
  }),
  owned_domains: z.array(z.string()),
  unintended_domains: z.array(z.string()),
  competitors: z.array(competitorSchema),
  prompt_sets: z.array(promptSetSchema),
  created_at: z.string(),
  updated_at: z.string(),
});

// ---------------------------------------------------------------------------
// Providers (BYOK) — secret never present
// ---------------------------------------------------------------------------

export const transportProviderSchema = z.enum(['anthropic', 'google', 'openrouter', 'openai']);
export const logicalEngineSchema = z.enum(['chatgpt', 'gemini', 'claude']);

// A configured route on a connection: which logical engine this transport
// serves and the concrete transport model to call.
export const providerRouteSchema = z.object({
  id: uuid(),
  logical_engine: logicalEngineSchema,
  transport_provider: transportProviderSchema,
  transport_model: z.string(),
  is_default: z.boolean(),
});

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

export const providerCatalogRouteSchema = z.object({
  transport_provider: transportProviderSchema,
  default_model: z.string(),
});

export const providerCatalogEngineSchema = z.object({
  logical_engine: logicalEngineSchema,
  routes: z.array(providerCatalogRouteSchema),
});

export const providerCatalogSchema = z.object({
  transports: z.array(transportProviderSchema),
  engines: z.array(providerCatalogEngineSchema),
});

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

export const auditSchema = z.object({
  id: uuid(),
  workspace_id: uuid(),
  project_id: uuid(),
  status: auditStatusSchema,
  random_seed: z.number(),
  configuration: z.record(z.string(), z.unknown()),
  summary: z.record(z.string(), z.unknown()).nullable(),
  requested_count: z.number().int(),
  completed_count: z.number().int(),
  failed_count: z.number().int(),
  error_message: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  completed_at: z.string().nullable(),
});

export const citationClassificationSchema = z.enum(['owned', 'competitor', 'third_party']);

export const citationSchema = z.object({
  ordinal: z.number().int(),
  url: z.string(),
  title: z.string().nullable(),
  domain: z.string(),
  cited_text: z.string().nullable(),
  classification: citationClassificationSchema,
});

export const searchEventSchema = z.object({
  query: z.string(),
  results: z.array(z.record(z.string(), z.unknown())).optional(),
});

export const executionStatusSchema = z.enum([
  'queued',
  'leased',
  'running',
  'succeeded',
  'retry_wait',
  'failed',
  'cancelled',
]);

export const executionSchema = z.object({
  id: uuid(),
  audit_id: uuid(),
  prompt_index: z.number().int(),
  repetition: z.number().int(),
  randomized_position: z.number().int(),
  status: executionStatusSchema,
  answer_text: z.string().nullable(),
  search_used: z.boolean(),
  search_events: z.array(searchEventSchema),
  citations: z.array(citationSchema),
  score: z.record(z.string(), z.unknown()).nullable(),
  provider_metadata: z.record(z.string(), z.unknown()).nullable(),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  latency_ms: z.number().nullable(),
});

// ---------------------------------------------------------------------------
// Visibility dashboard (selected-run projection)
// ---------------------------------------------------------------------------

export const visibilityEngineSchema = z.object({
  logical_engine: logicalEngineSchema,
  score: z.number().nullable(),
  brand_mention_rate: z.number().nullable(),
  owned_citation_rate: z.number().nullable(),
});

export const rankingRowSchema = z.object({
  entity_id: uuid().nullable(),
  name: z.string(),
  is_brand: z.boolean(),
  visibility: z.number().nullable(),
  share_of_voice: z.number().nullable(),
  sentiment: z.number().nullable(),
  avg_position: z.number().nullable(),
});

export const visibilitySchema = z.object({
  project_id: uuid(),
  audit_id: uuid(),
  score: z.number().nullable(),
  engines: z.array(visibilityEngineSchema),
  rankings: z.array(rankingRowSchema),
  generated_at: z.string(),
});

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
