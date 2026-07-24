import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { mswServer } from '@/test/msw-server';
import { opportunitiesApi } from './opportunities';
import { queryKeys } from './query-keys';
import {
  opportunitiesPageSchema,
  opportunityDetailSchema,
  opportunitySchema,
  opportunitySeveritySchema,
  opportunityStatusSchema,
  opportunitySummarySchema,
  opportunityTypeSchema,
  recomputeResponseSchema,
  strictValidate,
} from './schemas';

const PROJECT = '11111111-1111-4111-8111-111111111111';
const OPP = '22222222-2222-4222-8222-222222222222';
const AUDIT = '33333333-3333-4333-8333-333333333333';
const CRAWL = '44444444-4444-4444-8444-444444444444';

const item = {
  id: OPP,
  project_id: PROJECT,
  rule_id: 'brand_absent_high_value_prompt',
  opportunity_type: 'visibility' as const,
  severity: 'high' as const,
  priority_score: 120,
  title: 'Brand absent from high-value prompt',
  target_key: `prompt:${OPP}`,
  target_prompt_id: OPP,
  target_url: null,
  target_theme: 'crm',
  status: 'open' as const,
  created_at: '2026-07-24T00:00:00Z',
  updated_at: '2026-07-24T00:00:00Z',
};

const detail = {
  ...item,
  remediation: 'Publish a comparison page.',
  evidence: { prompt_text: 'best crm for small teams', competitor_names: ['Globex'] },
  source_analysis_ids: [AUDIT],
  source_issue_ids: [],
  source_metric_ids: [AUDIT],
  source_traffic_ids: [],
  analyzer_version: 'opp-analyzer-1',
  rule_version: 'opp-rules-1',
  formula_version: 'opp-formula-1',
  superseded_by_id: null,
  superseded_at: null,
};

const summary = {
  computed: true,
  run_id: AUDIT,
  audit_id: AUDIT,
  site_crawl_id: CRAWL,
  counts_by_type: { site: 2, topic: 0, traffic: 0, visibility: 2 },
  counts_by_severity: { critical: 0, high: 1, info: 0, low: 1, medium: 2 },
  counts_by_status: { dismissed: 0, in_progress: 0, open: 4, resolved: 0 },
  total_count: 4,
  median_priority: 50,
  analyzer_version: 'opp-analyzer-1',
  rule_version: 'opp-rules-1',
  formula_version: 'opp-formula-1',
  computed_at: '2026-07-24T00:00:00Z',
};

const recomputeResponse = {
  id: AUDIT,
  run_id: AUDIT,
  audit_id: AUDIT,
  site_crawl_id: CRAWL,
  counts_by_type: summary.counts_by_type,
  counts_by_severity: summary.counts_by_severity,
  counts_by_status: summary.counts_by_status,
  total_count: 4,
  median_priority: 50,
  analyzer_version: 'opp-analyzer-1',
  rule_version: 'opp-rules-1',
  formula_version: 'opp-formula-1',
  created_at: '2026-07-24T00:00:00Z',
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('opportunity schemas (strictValidate drift policy)', () => {
  it('accepts a valid catalog row', () => {
    expect(strictValidate(opportunitySchema, item, 'test')).toEqual(item);
    expect(strictValidate(opportunityDetailSchema, detail, 'test')).toEqual(detail);
    expect(
      strictValidate(opportunitiesPageSchema, { items: [item], next_cursor: null }, 'test'),
    ).toEqual({ items: [item], next_cursor: null });
    expect(strictValidate(opportunitySummarySchema, summary, 'test')).toEqual(summary);
    expect(strictValidate(recomputeResponseSchema, recomputeResponse, 'test')).toEqual(
      recomputeResponse,
    );
  });

  it('accepts the pre-recompute summary (computed=false, nulls)', () => {
    const empty = {
      ...summary,
      computed: false,
      run_id: null,
      audit_id: null,
      site_crawl_id: null,
      counts_by_type: {},
      counts_by_severity: {},
      counts_by_status: {},
      total_count: 0,
      median_priority: null,
      computed_at: null,
    };
    expect(strictValidate(opportunitySummarySchema, empty, 'test')).toEqual(empty);
  });

  it('fails loud on drift: extra keys, bad enums, non-uuid ids', () => {
    expect(() =>
      strictValidate(opportunitySchema, { ...item, unexpected: true }, 'test'),
    ).toThrow(/API validation failure/);
    expect(() =>
      strictValidate(opportunitySchema, { ...item, severity: 'urgent' }, 'test'),
    ).toThrow(/API validation failure/);
    expect(() =>
      strictValidate(opportunitySchema, { ...item, status: 'triaged' }, 'test'),
    ).toThrow(/API validation failure/);
    expect(() =>
      strictValidate(opportunitySchema, { ...item, opportunity_type: 'brand' }, 'test'),
    ).toThrow(/API validation failure/);
    expect(() =>
      strictValidate(opportunitySchema, { ...item, id: 'not-a-uuid' }, 'test'),
    ).toThrow(/API validation failure/);
    expect(() =>
      strictValidate(opportunitySummarySchema, { ...summary, extra: 1 }, 'test'),
    ).toThrow(/API validation failure/);
  });

  it('exposes the full vocabulary enums', () => {
    expect(opportunityTypeSchema.options).toEqual(['visibility', 'site', 'traffic', 'topic']);
    expect(opportunitySeveritySchema.options).toEqual([
      'critical',
      'high',
      'medium',
      'low',
      'info',
    ]);
    expect(opportunityStatusSchema.options).toEqual([
      'open',
      'in_progress',
      'dismissed',
      'resolved',
    ]);
  });
});

describe('opportunitiesApi transport', () => {
  it('builds the list query string from params', async () => {
    const seen: string[] = [];
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities`, ({ request }) => {
        seen.push(new URL(request.url).search);
        return HttpResponse.json({ items: [item], next_cursor: null });
      }),
    );

    const page = await opportunitiesApi.list(PROJECT, {
      type: 'site',
      severity: 'medium',
      status: 'open',
      rule_id: 'thin_content',
      min_priority: 30,
      limit: 25,
      cursor: 'abc',
    });
    expect(page.items).toHaveLength(1);
    expect(seen).toHaveLength(1);
    const params = new URLSearchParams(seen[0]);
    expect(params.get('type')).toBe('site');
    expect(params.get('severity')).toBe('medium');
    expect(params.get('status')).toBe('open');
    expect(params.get('rule_id')).toBe('thin_content');
    expect(params.get('min_priority')).toBe('30');
    expect(params.get('limit')).toBe('25');
    expect(params.get('cursor')).toBe('abc');
  });

  it('omits undefined params from the query string', async () => {
    const seen: string[] = [];
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities`, ({ request }) => {
        seen.push(new URL(request.url).search);
        return HttpResponse.json({ items: [], next_cursor: null });
      }),
    );
    await opportunitiesApi.list(PROJECT);
    expect(seen[0]).toBe('');
  });

  it('gets the detail and patches the status', async () => {
    const bodies: unknown[] = [];
    mswServer.use(
      http.get(`/api/v1/opportunities/${OPP}`, () => HttpResponse.json(detail)),
      http.patch(`/api/v1/opportunities/${OPP}`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({ ...item, status: 'in_progress' });
      }),
    );
    expect((await opportunitiesApi.get(OPP)).rule_id).toBe('brand_absent_high_value_prompt');
    const updated = await opportunitiesApi.updateStatus(OPP, 'in_progress');
    expect(updated.status).toBe('in_progress');
    expect(bodies).toEqual([{ status: 'in_progress' }]);
  });

  it('posts the recompute scope (default {}) and validates the snapshot', async () => {
    const bodies: unknown[] = [];
    mswServer.use(
      http.post(`/api/v1/projects/${PROJECT}/opportunities/recompute`, async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(recomputeResponse);
      }),
    );
    const result = await opportunitiesApi.recompute(PROJECT);
    expect(result.total_count).toBe(4);
    expect(bodies[0]).toEqual({});
    await opportunitiesApi.recompute(PROJECT, { audit_id: AUDIT });
    expect(bodies[1]).toEqual({ audit_id: AUDIT });
  });

  it('fails loud when the wire shape drifts (extra key)', async () => {
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/opportunities/summary`, () =>
        HttpResponse.json({ ...summary, extra: 'drift' }),
      ),
    );
    await expect(opportunitiesApi.summary(PROJECT)).rejects.toThrow(
      /API validation failure/,
    );
  });

  it('builds same-origin export URLs with optional filters', () => {
    expect(opportunitiesApi.exportUrl(PROJECT, 'csv')).toBe(
      `/api/v1/projects/${PROJECT}/opportunities/export.csv`,
    );
    expect(opportunitiesApi.exportUrl(PROJECT, 'md')).toBe(
      `/api/v1/projects/${PROJECT}/opportunities/export.md`,
    );
    const filtered = opportunitiesApi.exportUrl(PROJECT, 'csv', {
      type: 'site',
      severity: 'low',
    });
    expect(filtered.startsWith(`/api/v1/projects/${PROJECT}/opportunities/export.csv?`)).toBe(
      true,
    );
    const params = new URLSearchParams(filtered.split('?')[1]);
    expect(params.get('type')).toBe('site');
    expect(params.get('severity')).toBe('low');
  });
});

describe('opportunity query keys', () => {
  it('isolates namespaces by project / id / filters', () => {
    expect(queryKeys.opportunities.all).toEqual(['opportunities']);
    expect(queryKeys.opportunities.list(PROJECT, { severity: 'high' })).toEqual([
      'opportunities',
      'list',
      PROJECT,
      { severity: 'high' },
    ]);
    expect(queryKeys.opportunities.detail(OPP)).toEqual(['opportunities', 'detail', OPP]);
    expect(queryKeys.opportunities.summary(PROJECT)).toEqual([
      'opportunities',
      'summary',
      PROJECT,
    ]);
  });
});
