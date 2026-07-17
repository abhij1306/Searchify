import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import type { PageDetail } from '@/lib/api/types';
import { UrlDetail } from './url-detail';

const CRAWL = '44444444-4444-4444-8444-444444444444';
const URL_ID = 'cccccccc-1111-4111-8111-111111111111';
const ISSUE_H = 'aaaaaaaa-1111-4111-8111-111111111111';
const ISSUE_L = 'bbbbbbbb-1111-4111-8111-111111111111';

function detail(overrides: Partial<PageDetail> = {}): PageDetail {
  return {
    site_url_id: URL_ID,
    crawl_id: CRAWL,
    normalized_url: 'https://acme.com/',
    display_url: 'https://acme.com/',
    title: 'Best&Less Online',
    analysis_status: 'completed',
    error_code: '',
    field_cwv_available: false,
    technical_score: 46,
    aeo_score: 64,
    overall_score: 58,
    issue_count: 2,
    last_audited: '2026-07-16T00:00:00Z',
    facts: {
      title: 'Best&Less Online',
      meta_description: null,
      canonical_url: null,
      robots_directives: [],
      h1_count: 1,
      heading_count: 4,
      image_count: 3,
      image_missing_alt_count: 0,
      word_count: 500,
      internal_link_count: 10,
      external_link_count: 2,
      structured_data_types: [],
    },
    delivery: {
      field_cwv_available: false,
      status_code: 200,
      ttfb_ms: 840,
      wire_bytes: 40000,
      decoded_bytes: 145408,
      html_bytes: 145408,
      http_version: 'HTTP/2',
      compression: 'gzip',
      cache_control: 'no-cache',
      blocking_resource_count: 0,
    },
    issues: [
      {
        id: ISSUE_L,
        crawl_id: CRAWL,
        rule_id: 'aeo.faq',
        dimension: 'aeo',
        category: 'schema',
        severity: 'low',
        title: 'FAQ schema not present',
        remediation: '',
        affected_url_count: 1,
        analyzer_version: 'a1',
        rule_version: 'r1',
        created_at: '2026-07-16T00:00:00Z',
      },
      {
        id: ISSUE_H,
        crawl_id: CRAWL,
        rule_id: 'aeo.website_schema',
        dimension: 'aeo',
        category: 'schema',
        severity: 'high',
        title: 'WebSite schema is missing',
        remediation: '',
        affected_url_count: 1,
        analyzer_version: 'a1',
        rule_version: 'r1',
        created_at: '2026-07-16T00:00:00Z',
      },
    ],
    evaluations: [],
    link_references: [],
    artifact_id: null,
    extractor_version: 'e1',
    analyzer_version: 'a1',
    rule_version: 'r1',
    scoring_version: 's1',
    ...overrides,
  };
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

function handlers(pageDetail: PageDetail) {
  return [
    http.get(`/api/v1/site-crawls/${CRAWL}/pages/${URL_ID}`, () =>
      HttpResponse.json(pageDetail),
    ),
    http.get(`/api/v1/site-crawls/${CRAWL}/pages/${URL_ID}/issue-history`, () =>
      HttpResponse.json({ items: [], next_cursor: null }),
    ),
  ];
}

describe('UrlDetail', () => {
  it('renders scores, delivery metrics, and severity-ordered issues', async () => {
    mswServer.use(...handlers(detail()));

    renderWithProviders(<UrlDetail crawlId={CRAWL} siteUrlId={URL_ID} />);

    expect(await screen.findByRole('heading', { name: 'Best&Less Online', level: 1 })).toBeInTheDocument();
    // Delivery metric: TTFB rendered with ms suffix.
    expect(screen.getByText('840ms')).toBeInTheDocument();
    expect(screen.getByText('200')).toBeInTheDocument();
    // Issues section header shows the count.
    expect(screen.getByText('All Issues (2)')).toBeInTheDocument();

    // High-severity issue is ordered before the low-severity one.
    const high = screen.getByText('WebSite schema is missing');
    const low = screen.getByText('FAQ schema not present');
    expect(high.compareDocumentPosition(low) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders the "—" placeholder for a missing score, never a zero', async () => {
    mswServer.use(...handlers(detail({ technical_score: null, aeo_score: null, overall_score: null })));

    renderWithProviders(<UrlDetail crawlId={CRAWL} siteUrlId={URL_ID} />);

    await screen.findByRole('heading', { name: 'Best&Less Online', level: 1 });
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });
});
