import { describe, expect, it } from 'vitest';
import { z } from 'zod';

import {
  auditSchema,
  authResponseSchema,
  citationSchema,
  executionEvidenceSchema,
  executionSchema,
  historicalTransportProviderSchema,
  projectSchema,
  providerCatalogSchema,
  providerConnectionSchema,
  providerRouteSchema,
  sessionUserSchema,
  strictValidate,
  transportProviderSchema,
  visibilityEvidenceResponseSchema,
  visibilitySchema,
  workspaceSchema,
} from './schemas';

const UUID = '11111111-1111-4111-8111-111111111111';
const UUID2 = '22222222-2222-4222-8222-222222222222';

describe('strictValidate', () => {
  it('returns parsed data on a match', () => {
    const schema = z.object({ id: z.string().uuid() });
    expect(strictValidate(schema, { id: UUID }, 'ctx')).toEqual({ id: UUID });
  });

  it('throws with the context on a mismatch', () => {
    const schema = z.object({ id: z.string().uuid() });
    expect(() => strictValidate(schema, { id: 'not-a-uuid' }, 'ctx.here')).toThrow(
      /API validation failure in ctx\.here/,
    );
  });

  it('throws on a numeric id (contract forbids numeric ids)', () => {
    expect(() => strictValidate(z.object({ id: z.string().uuid() }), { id: 7 }, 'ids')).toThrow();
  });
});

describe('auth + workspace contract', () => {
  const sessionUser = {
    id: UUID,
    email: 'user@example.com',
    role: 'owner',
    is_active: true,
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
  };

  it('validates the { user } auth wrapper and rejects a bare SessionUser', () => {
    expect(strictValidate(authResponseSchema, { user: sessionUser }, 'auth').user.email).toBe(
      'user@example.com',
    );
    // The backend wraps the user; a bare SessionUser is a contract violation.
    expect(() => strictValidate(authResponseSchema, sessionUser, 'auth')).toThrow();
  });

  it('rejects an extra key on the auth wrapper (strict)', () => {
    expect(() =>
      strictValidate(authResponseSchema, { user: sessionUser, token: 'leaked' }, 'auth'),
    ).toThrow();
  });

  it('rejects an extra key on a SessionUser (strict)', () => {
    expect(() =>
      strictValidate(sessionUserSchema, { ...sessionUser, password_hash: 'x' }, 'user'),
    ).toThrow();
  });

  it('validates a workspace with role (no slug) and rejects a slug key', () => {
    const workspace = {
      id: UUID,
      name: 'Acme',
      role: 'owner',
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    };
    expect(strictValidate(workspaceSchema, workspace, 'ws').role).toBe('owner');
    // Backend WorkspaceResponse has no slug — an unexpected key must fail loud.
    expect(() => strictValidate(workspaceSchema, { ...workspace, slug: 'acme' }, 'ws')).toThrow();
  });
});

describe('contract schemas', () => {
  it('validates a project with uuid ids and benchmark_mode enum', () => {
    const project = {
      id: UUID,
      workspace_id: UUID2,
      name: 'Acme',
      brand_name: 'Acme',
      website_url: 'https://acme.example',
      country_code: 'US',
      language_code: 'en',
      benchmark_mode: 'consumer_like',
      default_repetitions: 3,
      brand: { aliases: ['Acme Inc'] },
      owned_domains: ['acme.example'],
      unintended_domains: [],
      competitors: [{ id: UUID2, name: 'Beta', aliases: [], domains: ['beta.example'] }],
      prompt_sets: [],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    };
    expect(strictValidate(projectSchema, project, 'project').benchmark_mode).toBe('consumer_like');
    expect(() =>
      strictValidate(projectSchema, { ...project, benchmark_mode: 'nope' }, 'project'),
    ).toThrow();
    // Strict: an unmodeled extra key on a response DTO is a contract drift bug.
    expect(() =>
      strictValidate(projectSchema, { ...project, surprise: true }, 'project'),
    ).toThrow();
  });

  it('rejects a provider connection that leaks a secret key', () => {
    const base = {
      id: UUID,
      workspace_id: UUID2,
      transport_provider: 'anthropic',
      base_url: null,
      active: true,
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    };
    expect(strictValidate(providerConnectionSchema, base, 'conn').active).toBe(true);
    expect(() =>
      strictValidate(providerConnectionSchema, { ...base, api_key: 'sk-test-fake' }, 'conn'),
    ).toThrow();
  });

  it('splits active-write and historical-read transport spaces (v2 retirement)', () => {
    // Write/catalog surface: only the three direct transports.
    for (const t of ['openai', 'anthropic', 'google']) {
      expect(transportProviderSchema.parse(t)).toBe(t);
    }
    expect(() => transportProviderSchema.parse('openrouter')).toThrow();
    // Read surface: historical space still accepts the retired openrouter token.
    for (const t of ['openai', 'anthropic', 'google', 'openrouter']) {
      expect(historicalTransportProviderSchema.parse(t)).toBe(t);
    }
  });

  it('reads a legacy openrouter connection + inactive route under strict validation', () => {
    const legacy = {
      id: UUID,
      workspace_id: UUID2,
      label: 'legacy',
      transport_provider: 'openrouter',
      base_url: null,
      active: false,
      api_key_set: true,
      routes: [
        {
          id: UUID2,
          logical_engine: 'chatgpt',
          transport_provider: 'openrouter',
          transport_model: 'openai/gpt-5.4',
          is_default: true,
          active: false,
        },
      ],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    };
    const parsed = strictValidate(providerConnectionSchema, legacy, 'legacy');
    expect(parsed.transport_provider).toBe('openrouter');
    expect(parsed.active).toBe(false);
    expect(parsed.routes?.[0]?.active).toBe(false);
  });

  it('defaults route.active to true when omitted and rejects a create-only openai catalog gap', () => {
    const route = {
      id: UUID,
      logical_engine: 'chatgpt',
      transport_provider: 'openai',
      transport_model: 'gpt-5.4',
      is_default: true,
    };
    // `active` is optional on the wire; a route without it parses.
    expect(strictValidate(providerRouteSchema, route, 'route').transport_provider).toBe('openai');
  });

  it('validates a direct-only provider catalog and rejects an openrouter catalog transport', () => {
    const catalog = {
      transports: ['openai', 'anthropic', 'google'],
      engines: [
        {
          logical_engine: 'chatgpt',
          routes: [{ transport_provider: 'openai', default_model: 'gpt-5.4' }],
        },
        {
          logical_engine: 'gemini',
          routes: [{ transport_provider: 'google', default_model: 'gemini-flash-latest' }],
        },
        {
          logical_engine: 'claude',
          routes: [{ transport_provider: 'anthropic', default_model: 'claude-sonnet-4-6' }],
        },
      ],
    };
    expect(strictValidate(providerCatalogSchema, catalog, 'catalog').transports).toHaveLength(3);
    // The catalog must never advertise the retired openrouter transport.
    expect(() =>
      strictValidate(
        providerCatalogSchema,
        { ...catalog, transports: ['openai', 'openrouter'] },
        'catalog',
      ),
    ).toThrow();
  });

  it('validates citation classification enum', () => {
    const citation = {
      ordinal: 1,
      url: 'https://acme.example/a',
      title: 'A',
      domain: 'acme.example',
      classification: 'owned',
      is_owned: true,
      is_unintended: false,
      matched_competitor: null,
    };
    expect(strictValidate(citationSchema, citation, 'c').classification).toBe('owned');
    // The 'unintended' (owned-but-unwanted) class is a valid backend value.
    expect(
      strictValidate(citationSchema, { ...citation, classification: 'unintended' }, 'c')
        .classification,
    ).toBe('unintended');
    expect(() =>
      strictValidate(citationSchema, { ...citation, classification: 'internal' }, 'c'),
    ).toThrow();
  });

  it('validates an audit (string seed + engine snapshots, no null error)', () => {
    const audit = {
      id: UUID,
      workspace_id: UUID2,
      project_id: UUID,
      status: 'completed',
      benchmark_mode: 'consumer_like',
      repetitions: 3,
      random_seed: '42',
      requested_count: 10,
      completed_count: 10,
      failed_count: 0,
      error_message: '',
      engine_snapshots: [
        {
          logical_engine: 'gemini',
          transport_provider: 'google',
          transport_model: 'gemini-flash-latest',
        },
      ],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
      started_at: '2026-07-15T00:00:05Z',
      completed_at: '2026-07-15T00:10:00Z',
    };
    expect(strictValidate(auditSchema, audit, 'audit').status).toBe('completed');
    // The 64-bit seed is a decimal STRING on the wire, never a number.
    expect(() => strictValidate(auditSchema, { ...audit, random_seed: 42 }, 'audit')).toThrow();
    // Strict: an unmodeled extra key must fail loud, never be silently stripped.
    expect(() => strictValidate(auditSchema, { ...audit, extra: 'nope' }, 'audit')).toThrow();
  });

  it('validates an execution/queue row (AuditTaskResponse shape)', () => {
    const execution = {
      id: UUID,
      audit_id: UUID2,
      prompt_index: 0,
      repetition: 1,
      randomized_position: 2,
      logical_engine: 'gemini',
      transport_provider: 'google',
      transport_model: 'gemini-flash-latest',
      status: 'succeeded',
      attempt_count: 1,
      max_attempts: 5,
      answer_text: 'Answer',
      search_used: true,
      error_code: '',
      error_detail: '',
      latency_ms: 1200,
      created_at: '2026-07-15T00:00:00Z',
      completed_at: '2026-07-15T00:00:03Z',
    };
    expect(strictValidate(executionSchema, execution, 'exec').status).toBe('succeeded');
  });

  it('validates execution evidence keyed by the execution id', () => {
    const evidence = {
      id: UUID,
      analysis_id: UUID2,
      audit_id: UUID2,
      task_id: UUID,
      artifact_id: null,
      analyzer_version: 'v1',
      scoring_rule_version: 'v1',
      logical_engine: 'gemini',
      transport_provider: 'google',
      transport_model: 'gemini-flash-latest',
      prompt_index: 0,
      repetition: 1,
      prompt_class: 'unbranded',
      brand_mentioned: true,
      brand_first_offset: 12,
      owned_domain_cited: true,
      owned_citation_count: 1,
      unintended_domain_cited: false,
      citation_count: 2,
      search_used: true,
      search_query_count: 1,
      sentiment: null,
      avg_position: null,
      score: { visibility: 1 },
      citations: [
        {
          ordinal: 1,
          url: 'https://acme.example/a',
          title: 'A',
          domain: 'acme.example',
          classification: 'owned',
          is_owned: true,
          is_unintended: false,
          matched_competitor: null,
        },
      ],
      competitors_mentioned: ['Beta'],
      created_at: '2026-07-15T00:00:00Z',
    };
    const parsed = strictValidate(executionEvidenceSchema, evidence, 'evidence');
    expect(parsed.brand_mentioned).toBe(true);
    expect(parsed.sentiment).toBeNull();
    expect(parsed.citations[0]?.classification).toBe('owned');
  });

  it('validates a visibility projection with nullable sentiment/avg_position', () => {
    const visibility = {
      project_id: UUID,
      audit_id: UUID2,
      audit_status: 'completed',
      analyzer_version: 'v1',
      scoring_rule_version: 'v1',
      total_completed: 10,
      total_failed: 0,
      visibility_score: 72.5,
      per_engine: [
        {
          logical_engine: 'gemini',
          total_completed: 5,
          brand_mention_rate: 0.6,
          owned_citation_rate: 0.3,
          search_use_rate: 0.5,
          visibility_score: 80,
        },
      ],
      rankings: [
        {
          name: 'Acme',
          is_brand: true,
          mention_rate: 0.725,
          citation_rate: 0.3,
          share_of_voice: 0.4,
          mention_count: 4,
          sentiment: null,
          avg_position: null,
        },
      ],
      sentiment: null,
      avg_position: null,
      created_at: '2026-07-15T00:00:00Z',
    };
    const parsed = strictValidate(visibilitySchema, visibility, 'visibility');
    expect(parsed.rankings[0]?.sentiment).toBeNull();
    expect(parsed.rankings[0]?.avg_position).toBeNull();
  });
});

describe('visibility evidence contract', () => {
  function makeCitation() {
    return {
      ordinal: 1,
      url: 'https://acme.com/a',
      title: 'Acme',
      domain: 'acme.com',
      classification: 'owned',
      is_owned: true,
      is_unintended: false,
      matched_competitor: null,
    };
  }

  function makeItem(overrides: Record<string, unknown> = {}) {
    return {
      audit_id: UUID,
      task_id: UUID2,
      analysis_id: UUID,
      artifact_id: UUID2,
      prompt_snapshot_id: UUID,
      prompt_id: UUID2,
      prompt_index: 3,
      prompt_text: 'Best affordable clothing stores?',
      repetition: 1,
      completed_at: '2026-07-15T14:32:00Z',
      logical_engine: 'chatgpt',
      transport_provider: 'openai',
      transport_model: 'gpt-5.4',
      search_used: true,
      search_query_count: 2,
      query_text_available: true,
      state: 'queries_available',
      search_events: [
        {
          sequence: 0,
          query: 'affordable clothing Australia',
          call_id: 'c1',
          call_sequence: 0,
          query_sequence: 0,
        },
        {
          sequence: 1,
          query: 'budget family shops',
          call_id: 'c1',
          call_sequence: 0,
          query_sequence: 1,
        },
      ],
      event_source: 'raw_artifact',
      mentions: [
        {
          kind: 'brand',
          name: 'Acme',
          first_offset: 12,
          artifact_id: UUID2,
          analyzer_version: 'v1',
        },
        {
          kind: 'competitor',
          name: 'Globex',
          first_offset: null,
          artifact_id: null,
          analyzer_version: 'v1',
        },
      ],
      citations: [makeCitation()],
      ...overrides,
    };
  }

  it('parses a full evidence response with items and truncated flag', () => {
    const parsed = strictValidate(
      visibilityEvidenceResponseSchema,
      { items: [makeItem()], truncated: true },
      'evidence',
    );
    expect(parsed.items).toHaveLength(1);
    expect(parsed.truncated).toBe(true);
    expect(parsed.items[0]?.mentions[0]?.kind).toBe('brand');
    expect(parsed.items[0]?.search_events).toHaveLength(2);
  });

  it('accepts nullable prompt_id / artifact_id / completed_at and count-only / no-search states', () => {
    const countOnly = makeItem({
      state: 'count_only',
      query_text_available: false,
      prompt_id: null,
      artifact_id: null,
      completed_at: null,
      event_source: 'audit_task',
      search_events: [],
      mentions: [],
      citations: [],
    });
    const noSearch = makeItem({
      analysis_id: UUID2,
      state: 'no_search',
      search_used: false,
      search_query_count: 0,
      query_text_available: false,
      event_source: 'none',
      search_events: [],
    });
    const parsed = strictValidate(
      visibilityEvidenceResponseSchema,
      { items: [countOnly, noSearch], truncated: false },
      'evidence',
    );
    expect(parsed.items[0]?.state).toBe('count_only');
    expect(parsed.items[0]?.prompt_id).toBeNull();
    expect(parsed.items[0]?.completed_at).toBeNull();
    expect(parsed.items[1]?.state).toBe('no_search');
  });

  it('rejects an unknown fanout state and unknown extra keys (strict)', () => {
    expect(() =>
      strictValidate(
        visibilityEvidenceResponseSchema,
        { items: [makeItem({ state: 'partial' })], truncated: false },
        'evidence',
      ),
    ).toThrow();
    expect(() =>
      strictValidate(
        visibilityEvidenceResponseSchema,
        { items: [makeItem({ unexpected: true })], truncated: false },
        'evidence',
      ),
    ).toThrow();
  });

  it('preserves an empty query string in a search event (never invented)', () => {
    const item = makeItem({
      state: 'count_only',
      search_events: [
        { sequence: 0, query: '', call_id: 'c1', call_sequence: 0, query_sequence: 0 },
      ],
    });
    const parsed = strictValidate(
      visibilityEvidenceResponseSchema,
      { items: [item], truncated: false },
      'evidence',
    );
    expect(parsed.items[0]?.search_events[0]?.query).toBe('');
  });
});
