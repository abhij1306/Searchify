import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
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
  dimension_counts: { technical: 30, aeo: 17 },
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
    // Chip counts (tiles removed): All (47), Medium (23), AEO (17). The counts
    // come from the API-owned summary, not a client re-count.
    expect(screen.getByRole('button', { name: 'All (47)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Medium (23)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'AEO (17)' })).toBeInTheDocument();
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

    await user.click(screen.getByRole('button', { name: 'Medium (23)' }));
    await waitFor(() => expect(seen).toContain('medium'));
  });

  it('wires the page-type filter as a server param, set and cleared', async () => {
    const seen: Array<string | null> = [];
    mswServer.use(
      http.get(`/api/v1/site-crawls/${CRAWL}/issues`, ({ request }) => {
        seen.push(new URL(request.url).searchParams.get('page_type'));
        return HttpResponse.json({ items: [issue()], next_cursor: null, summary });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<IssuesCatalog crawlId={CRAWL} />);
    const select = await screen.findByLabelText('Filter by page type');
    // The initial unfiltered request carries no page-type param.
    await screen.findByText('WebSite schema is missing');
    expect(seen.at(-1)).toBeNull();

    await user.selectOptions(select, 'article');
    await waitFor(() => expect(seen.at(-1)).toBe('article'));

    // Clearing back to "All page types" drops the param entirely. The
    // unfiltered combination is already cached (no new request), so force a
    // fresh combination via a chip and assert THAT request omits page_type.
    // (Select by the option's visible label — user-event does not fire a
    // change event for selectOptions(select, '').)
    await user.selectOptions(select, 'All page types');
    expect(select).toHaveValue('');
    await user.click(screen.getByRole('button', { name: 'Medium (23)' }));
    await waitFor(() => expect(seen.at(-1)).toBeNull());
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
              page_type: 'article',
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
    // The affected page's v2 P1 type badge renders inside the row (scoped —
    // the filter <select> also lists the type label as an option).
    expect(within(link).getByText('Article')).toBeInTheDocument();
  });

  it('pages affected URLs with a cursor-aware Next/Previous control', async () => {
    const seenCursors: (string | null)[] = [];
    mswServer.use(
      http.get(`/api/v1/site-crawls/${CRAWL}/issues`, () =>
        HttpResponse.json({ items: [issue()], next_cursor: null, summary }),
      ),
      http.get(`/api/v1/site-crawls/${CRAWL}/issues/${ISSUE_A}`, ({ request }) => {
        const url = new URL(request.url);
        const cursor = url.searchParams.get('cursor');
        seenCursors.push(cursor);
        const onFirstPage = cursor === null;
        return HttpResponse.json({
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
              site_url_id: onFirstPage ? URL_A : 'cccccccc-2222-4111-8111-111111111111',
              normalized_url: onFirstPage ? 'https://acme.com/' : 'https://acme.com/page-2',
              display_url: onFirstPage ? 'https://acme.com/' : 'https://acme.com/page-2',
              title: onFirstPage ? 'Homepage' : 'Page Two',
            },
          ],
          affected_url_count: 2,
          analyzer_version: 'a1',
          rule_version: 'r1',
          created_at: '2026-07-15T00:00:00Z',
          next_cursor: onFirstPage ? 'cursor-page-2' : null,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<IssuesCatalog crawlId={CRAWL} />);
    await screen.findByText('WebSite schema is missing');

    await user.click(screen.getByRole('button', { name: 'View affected URLs' }));
    await screen.findByRole('link', { name: /Homepage/ });

    // Two Previous/Next pairs exist on screen: the issue-list pager (outer)
    // and the affected-URLs pager (inner, inside the expanded card). The
    // inner one renders first in DOM order since it's inside the card.
    const [innerPrev] = screen.getAllByRole('button', { name: 'Previous' });
    const [innerNext] = screen.getAllByRole('button', { name: 'Next' });
    expect(innerPrev).toBeDisabled();
    expect(innerNext).not.toBeDisabled();

    await user.click(innerNext);
    await screen.findByRole('link', { name: /Page Two/ });
    expect(seenCursors).toEqual([null, 'cursor-page-2']);
    const [innerPrevAfterNext] = screen.getAllByRole('button', { name: 'Previous' });
    const [innerNextAfterNext] = screen.getAllByRole('button', { name: 'Next' });
    expect(innerPrevAfterNext).not.toBeDisabled();
    expect(innerNextAfterNext).toBeDisabled();

    await user.click(innerPrevAfterNext);
    await screen.findByRole('link', { name: /Homepage/ });
    // Going back to cursor=null is served from the TanStack Query cache
    // (already fetched above), so no new request is issued — seenCursors
    // stays at the two requests already made.
    expect(seenCursors).toEqual([null, 'cursor-page-2']);
  });
});
