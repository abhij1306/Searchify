import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import type { SiteIssue } from '@/lib/api/types';
import { IssuesCatalog } from './issues-catalog';

const CRAWL = '44444444-4444-4444-8444-444444444444';
const ISSUE_A = 'aaaaaaaa-1111-4111-8111-111111111111';
const URL_A = 'cccccccc-1111-4111-8111-111111111111';

function issue(overrides: Partial<SiteIssue> = {}): SiteIssue {
  return {
    id: ISSUE_A,
    crawl_id: CRAWL,
    rule_id: 'aeo.website_schema',
    dimension: 'aeo',
    category: 'schema',
    severity: 'high',
    title: 'WebSite schema is missing',
    remediation: 'Add a JSON-LD WebSite schema.',
    affected_url_count: 32,
    analyzer_version: 'a1',
    rule_version: 'r1',
    created_at: '2026-07-15T00:00:00Z',
    ...overrides,
  };
}

const summary = {
  issue_count: 47,
  severity_counts: { high: 12, medium: 23, low: 12 },
  affected_url_count: 50,
  monitored_affected_url_count: 38,
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('IssuesCatalog', () => {
  it('renders the API-owned summary and grouped issue rows', async () => {
    mswServer.use(
      http.get(`/api/v1/site-crawls/${CRAWL}/issues`, () =>
        HttpResponse.json({ items: [issue()], next_cursor: null, summary }),
      ),
    );

    renderWithProviders(<IssuesCatalog crawlId={CRAWL} />);

    expect(await screen.findByText('WebSite schema is missing')).toBeInTheDocument();
    // Summary tiles: total, high (12), medium (23), pages affected (38).
    expect(screen.getByText('47')).toBeInTheDocument();
    expect(screen.getByText('23')).toBeInTheDocument();
    expect(screen.getByText('38')).toBeInTheDocument();
    // Severity + dimension badges + affected-count copy.
    expect(screen.getByText('HIGH')).toBeInTheDocument();
    expect(screen.getAllByText('AEO').length).toBeGreaterThan(0);
    expect(screen.getByText('32 pages affected')).toBeInTheDocument();
    // No unsupported "mark reviewed/resolved" action is rendered.
    expect(screen.queryByText(/mark reviewed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/mark resolved/i)).not.toBeInTheDocument();
  });

  it('applies a severity filter as a server param (not a client filter)', async () => {
    const seen: string[] = [];
    mswServer.use(
      http.get(`/api/v1/site-crawls/${CRAWL}/issues`, ({ request }) => {
        const url = new URL(request.url);
        seen.push(url.searchParams.get('severity') ?? '');
        return HttpResponse.json({ items: [issue()], next_cursor: null, summary });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<IssuesCatalog crawlId={CRAWL} />);
    await screen.findByText('WebSite schema is missing');

    await user.click(screen.getByRole('button', { name: 'Medium' }));
    await waitFor(() => expect(seen).toContain('medium'));
  });

  it('expands affected URLs linking to the per-URL detail route', async () => {
    mswServer.use(
      http.get(`/api/v1/site-crawls/${CRAWL}/issues`, () =>
        HttpResponse.json({ items: [issue()], next_cursor: null, summary }),
      ),
      http.get(`/api/v1/site-crawls/${CRAWL}/issues/${ISSUE_A}`, () =>
        HttpResponse.json({
          id: ISSUE_A,
          crawl_id: CRAWL,
          rule_id: 'aeo.website_schema',
          dimension: 'aeo',
          category: 'schema',
          severity: 'high',
          title: 'WebSite schema is missing',
          remediation: 'Add a JSON-LD WebSite schema.',
          evidence: {},
          affected_urls: [
            {
              site_url_id: URL_A,
              normalized_url: 'https://acme.com/',
              display_url: 'https://acme.com/',
              title: 'Homepage',
            },
          ],
          affected_url_count: 1,
          analyzer_version: 'a1',
          rule_version: 'r1',
          created_at: '2026-07-15T00:00:00Z',
          next_cursor: null,
        }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders(<IssuesCatalog crawlId={CRAWL} />);
    await screen.findByText('WebSite schema is missing');

    await user.click(screen.getByRole('button', { name: 'View affected URLs' }));

    const link = await screen.findByRole('link', { name: /Homepage/ });
    expect(link).toHaveAttribute('href', `/site-health/crawls/${CRAWL}/pages/${URL_A}`);
  });
});
