import { describe, expect, it } from 'vitest';

import { queryKeys } from './query-keys';
import {
  cursorPageSchema,
  inventoryRowSchema,
  monitoredUrlsResponseSchema,
  pageDetailSchema,
  rerunPageResponseSchema,
  siteCrawlSchema,
  siteHealthEntitlementSchema,
  siteHealthErrorSchema,
  siteIssueSchema,
  strictValidate,
} from './schemas';
import { z } from 'zod';

const UUID = '11111111-1111-4111-8111-111111111111';
const UUID2 = '22222222-2222-4222-8222-222222222222';

const entitlement = {
  workspace_id: UUID,
  plan_key: 'starter' as const,
  access_mode: 'selection' as const,
  sample_url_limit: 10,
  monitored_url_limit: 50,
  can_view_discovered_total: true,
  capability_revision: 3,
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
};

const crawl = {
  id: UUID,
  workspace_id: UUID,
  project_id: UUID2,
  profile_id: UUID2,
  status: 'running' as const,
  discovery_status: 'running' as const,
  analysis_status: 'pending' as const,
  root_url: 'https://example.com/',
  sample_mode: false,
  seed: '12345',
  inventory_complete: false,
  visible_url_count: 42,
  analyzed_count: 0,
  failed_count: 0,
  total_url_count: null,
  score_summary: null,
  extractor_version: 'x1',
  analyzer_version: 'a1',
  rule_version: 'r1',
  scoring_version: 's1',
  error_message: '',
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
  started_at: null,
  completed_at: null,
};

const inventoryRow = {
  site_url_id: UUID,
  normalized_url: 'https://example.com/a',
  display_url: 'https://example.com/a',
  title: null,
  content_type: null,
  source: 'link' as const,
  depth: 1,
  monitored: false,
  first_seen_at: null,
  last_seen_at: null,
  issue_count: null,
  technical_score: null,
  aeo_score: null,
  overall_score: null,
  last_audited: null,
};

describe('siteHealthEntitlementSchema (quota authority)', () => {
  it('accepts a valid entitlement and exposes monitored_url_limit', () => {
    const parsed = strictValidate(siteHealthEntitlementSchema, entitlement, 'ent');
    expect(parsed.monitored_url_limit).toBe(50);
  });

  it('rejects an extra key (strict)', () => {
    expect(() =>
      strictValidate(siteHealthEntitlementSchema, { ...entitlement, hardcoded_50: true }, 'ent'),
    ).toThrow();
  });

  it('rejects a missing required field', () => {
    const { monitored_url_limit: _omit, ...rest } = entitlement;
    expect(() => strictValidate(siteHealthEntitlementSchema, rest, 'ent')).toThrow();
  });

  it('rejects an unknown plan_key', () => {
    expect(() =>
      strictValidate(
        siteHealthEntitlementSchema,
        { ...entitlement, plan_key: 'enterprise' },
        'ent',
      ),
    ).toThrow();
  });
});

describe('siteCrawlSchema (Free redaction / nullable totals)', () => {
  it('accepts a running crawl with a null total (provisional)', () => {
    const parsed = strictValidate(siteCrawlSchema, crawl, 'crawl');
    expect(parsed.total_url_count).toBeNull();
  });

  it('accepts a Free sample crawl with total_url_count null and no leaked total', () => {
    const sample = { ...crawl, sample_mode: true, inventory_complete: true, total_url_count: null };
    expect(strictValidate(siteCrawlSchema, sample, 'crawl').sample_mode).toBe(true);
  });

  it('rejects an unexpected count-bearing key on a crawl (strict)', () => {
    expect(() =>
      strictValidate(siteCrawlSchema, { ...crawl, hidden_full_total: 9999 }, 'crawl'),
    ).toThrow();
  });
});

describe('inventoryRowSchema (nullable analysis summaries)', () => {
  it('accepts null analysis summaries before analysis completes', () => {
    const parsed = strictValidate(inventoryRowSchema, inventoryRow, 'row');
    expect(parsed.overall_score).toBeNull();
    expect(parsed.issue_count).toBeNull();
  });

  it('accepts populated analysis summaries after analysis', () => {
    const analysed = {
      ...inventoryRow,
      issue_count: 3,
      technical_score: 88.5,
      aeo_score: 72,
      overall_score: 80.2,
      last_audited: '2026-07-15T00:00:00Z',
    };
    expect(strictValidate(inventoryRowSchema, analysed, 'row').issue_count).toBe(3);
  });

  it('rejects an extra key on an inventory row', () => {
    expect(() =>
      strictValidate(inventoryRowSchema, { ...inventoryRow, sort_rank: 1 }, 'row'),
    ).toThrow();
  });
});

describe('cursorPageSchema', () => {
  const page = cursorPageSchema(inventoryRowSchema);

  it('accepts a page with a null next_cursor (last page)', () => {
    const parsed = strictValidate(page, { items: [inventoryRow], next_cursor: null }, 'page');
    expect(parsed.next_cursor).toBeNull();
  });

  it('accepts a page with a cursor', () => {
    const parsed = strictValidate(page, { items: [], next_cursor: 'opaque==' }, 'page');
    expect(parsed.next_cursor).toBe('opaque==');
  });

  it('rejects an offset / page-total field (no count side channel)', () => {
    expect(() =>
      strictValidate(page, { items: [], next_cursor: null, total: 25000 }, 'page'),
    ).toThrow();
  });
});

describe('monitoredUrlsResponseSchema', () => {
  const response = {
    project_id: UUID,
    selection_version: 4,
    monitored_urls: [
      {
        site_url_id: UUID2,
        normalized_url: 'https://example.com/',
        display_url: 'https://example.com/',
        title: 'Home',
        active: true,
        selection_source: 'user' as const,
        selected_at: '2026-07-15T00:00:00Z',
        deselected_at: null,
      },
    ],
    quota: { used: 1, limit: 50 },
  };

  it('accepts a monitored set with quota + version', () => {
    const parsed = strictValidate(monitoredUrlsResponseSchema, response, 'mon');
    expect(parsed.quota.limit).toBe(50);
    expect(parsed.selection_version).toBe(4);
  });

  it('rejects an invalid selection_source', () => {
    const bad = {
      ...response,
      monitored_urls: [{ ...response.monitored_urls[0], selection_source: 'admin' }],
    };
    expect(() => strictValidate(monitoredUrlsResponseSchema, bad, 'mon')).toThrow();
  });
});

describe('pageDetailSchema (field_cwv_available literal false)', () => {
  const detail = {
    site_url_id: UUID,
    crawl_id: UUID2,
    normalized_url: 'https://example.com/',
    display_url: 'https://example.com/',
    title: 'Home',
    analysis_status: 'completed' as const,
    error_code: '',
    field_cwv_available: false as const,
    technical_score: 90,
    aeo_score: 80,
    overall_score: 85,
    issue_count: 2,
    last_audited: '2026-07-15T00:00:00Z',
    facts: {
      title: 'Home',
      meta_description: null,
      canonical_url: null,
      robots_directives: [],
      h1_count: 1,
      heading_count: 5,
      image_count: 3,
      image_missing_alt_count: 0,
      word_count: 500,
      internal_link_count: 10,
      external_link_count: 2,
      structured_data_types: ['Organization'],
    },
    delivery: {
      field_cwv_available: false as const,
      status_code: 200,
      ttfb_ms: 120,
      wire_bytes: 4096,
      decoded_bytes: 8192,
      html_bytes: 8192,
      http_version: 'HTTP/2',
      compression: 'gzip',
      cache_control: 'max-age=3600',
      blocking_resource_count: 1,
    },
    issues: [],
    evaluations: [],
    link_references: [],
    artifact_id: UUID,
    extractor_version: 'x1',
    analyzer_version: 'a1',
    rule_version: 'r1',
    scoring_version: 's1',
  };

  it('accepts a full page detail with field_cwv_available false', () => {
    expect(strictValidate(pageDetailSchema, detail, 'page').field_cwv_available).toBe(false);
  });

  it('rejects field_cwv_available true (crawler never fabricates field CWV)', () => {
    expect(() =>
      strictValidate(pageDetailSchema, { ...detail, field_cwv_available: true }, 'page'),
    ).toThrow();
  });

  it('rejects a leaked LCP field (strict)', () => {
    expect(() => strictValidate(pageDetailSchema, { ...detail, lcp_ms: 1200 }, 'page')).toThrow();
  });
});

describe('rerunPageResponseSchema (rerun identity/status)', () => {
  const base = {
    crawl_id: UUID,
    site_url_id: UUID2,
    task_id: UUID,
    created_new_crawl: true,
    analysis_status: 'pending' as const,
  };

  it('accepts a fresh-crawl rerun response', () => {
    const parsed = strictValidate(rerunPageResponseSchema, base, 'rerun');
    expect(parsed.created_new_crawl).toBe(true);
    expect(parsed.crawl_id).toBe(UUID);
  });

  it('accepts a same-active-crawl rerun response', () => {
    const parsed = strictValidate(
      rerunPageResponseSchema,
      { ...base, created_new_crawl: false, analysis_status: 'running' },
      'rerun',
    );
    expect(parsed.created_new_crawl).toBe(false);
  });

  it('rejects an unknown analysis_status', () => {
    expect(() =>
      strictValidate(rerunPageResponseSchema, { ...base, analysis_status: 'queued' }, 'rerun'),
    ).toThrow();
  });

  it('rejects an extra field (strict)', () => {
    expect(() =>
      strictValidate(rerunPageResponseSchema, { ...base, new_crawl_id: UUID }, 'rerun'),
    ).toThrow();
  });
});

describe('siteIssueSchema + siteHealthErrorSchema', () => {
  it('accepts a valid issue row', () => {
    const issue = {
      id: UUID,
      crawl_id: UUID2,
      rule_id: 'meta.title.missing',
      dimension: 'aeo' as const,
      category: 'metadata',
      severity: 'high' as const,
      title: 'Missing title',
      remediation: 'Add a <title>.',
      affected_url_count: 4,
      analyzer_version: 'a1',
      rule_version: 'r1',
      created_at: '2026-07-15T00:00:00Z',
    };
    expect(strictValidate(siteIssueSchema, issue, 'issue').severity).toBe('high');
  });

  it('accepts a quota error carrying limit + currently_used', () => {
    const err = {
      code: 'site_health_quota_exceeded' as const,
      message: 'over',
      limit: 50,
      currently_used: 50,
    };
    expect(strictValidate(siteHealthErrorSchema, err, 'err').limit).toBe(50);
  });

  it('accepts a stale-selection error carrying versions', () => {
    const err = {
      code: 'stale_selection_version' as const,
      message: 'stale',
      expected_selection_version: 3,
      current_selection_version: 5,
    };
    expect(strictValidate(siteHealthErrorSchema, err, 'err').current_selection_version).toBe(5);
  });

  it('rejects an unknown error code', () => {
    expect(() =>
      strictValidate(siteHealthErrorSchema, { code: 'kaboom', message: 'x' }, 'err'),
    ).toThrow();
  });
});

describe('query key isolation (project / crawl / filter)', () => {
  it('isolates entitlements from everything else', () => {
    expect(queryKeys.siteHealth.entitlements(null)).toEqual([
      'site-health',
      'entitlements',
      'default',
    ]);
  });

  it('isolates entitlements by workspace id', () => {
    expect(queryKeys.siteHealth.entitlements('ws-1')).not.toEqual(
      queryKeys.siteHealth.entitlements('ws-2'),
    );
  });

  it('isolates inventory by crawl', () => {
    expect(queryKeys.siteHealth.inventory('c1')).not.toEqual(queryKeys.siteHealth.inventory('c2'));
  });

  it('isolates inventory by filter', () => {
    const a = queryKeys.siteHealth.inventory('c1', { query: 'foo' });
    const b = queryKeys.siteHealth.inventory('c1', { query: 'bar' });
    expect(a).not.toEqual(b);
  });

  it('isolates dashboard by project and crawl', () => {
    expect(queryKeys.siteHealth.dashboard('p1')).toEqual([
      'site-health',
      'dashboard',
      'p1',
      'latest',
    ]);
    expect(queryKeys.siteHealth.dashboard('p1', 'c1')).not.toEqual(
      queryKeys.siteHealth.dashboard('p1'),
    );
  });

  it('isolates issues by crawl and filter', () => {
    const a = queryKeys.siteHealth.issues('c1', { severity: 'high' });
    const b = queryKeys.siteHealth.issues('c1', { severity: 'low' });
    const c = queryKeys.siteHealth.issues('c2', { severity: 'high' });
    expect(a).not.toEqual(b);
    expect(a).not.toEqual(c);
  });

  it('keeps monitored keyed per project', () => {
    expect(queryKeys.siteHealth.monitored('p1')).not.toEqual(queryKeys.siteHealth.monitored('p2'));
  });
});

// Sanity: cursorPageSchema is generic and composes with any item schema.
describe('cursorPageSchema generics', () => {
  it('composes with a trivial item schema', () => {
    const page = cursorPageSchema(z.object({ x: z.number() }).strict());
    expect(strictValidate(page, { items: [{ x: 1 }], next_cursor: null }, 'p').items[0].x).toBe(1);
  });
});
