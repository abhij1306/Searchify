import { describe, expect, it } from 'vitest';
import { z } from 'zod';

import {
  auditSchema,
  citationSchema,
  executionEvidenceSchema,
  executionSchema,
  projectSchema,
  providerConnectionSchema,
  strictValidate,
  visibilitySchema,
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
      strictValidate(providerConnectionSchema, { ...base, api_key: 'sk-secret' }, 'conn'),
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
        { logical_engine: 'gemini', transport_provider: 'google', transport_model: 'gemini-flash-latest' },
      ],
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
      started_at: '2026-07-15T00:00:05Z',
      completed_at: '2026-07-15T00:10:00Z',
    };
    expect(strictValidate(auditSchema, audit, 'audit').status).toBe('completed');
    // The 64-bit seed is a decimal STRING on the wire, never a number.
    expect(() => strictValidate(auditSchema, { ...audit, random_seed: 42 }, 'audit')).toThrow();
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
