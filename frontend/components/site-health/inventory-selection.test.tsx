import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import type { InventoryRow, SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import { InventorySelection } from './inventory-selection';

const PROJECT = '22222222-2222-4222-8222-222222222222';
const URL_A = 'aaaaaaaa-1111-4111-8111-111111111111';
const URL_B = 'bbbbbbbb-1111-4111-8111-111111111111';

const entitlement: SiteHealthEntitlement = {
  workspace_id: '33333333-3333-4333-8333-333333333333',
  plan_key: 'starter',
  access_mode: 'selection',
  sample_url_limit: 10,
  monitored_url_limit: 50,
  can_view_discovered_total: true,
  capability_revision: 1,
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
};

const crawl = {
  id: '44444444-4444-4444-8444-444444444444',
  project_id: PROJECT,
  root_url: 'https://acme.com/',
} as unknown as SiteCrawl;

function row(id: string, url: string): InventoryRow {
  return {
    site_url_id: id,
    normalized_url: url,
    display_url: url,
    title: null,
    content_type: 'text/html',
    source: 'link',
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
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('InventorySelection', () => {
  it('commits the full versioned monitored set (not just visible rows)', async () => {
    const user = userEvent.setup();
    const putBody = vi.fn();
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/monitored-urls`, () =>
        HttpResponse.json({
          project_id: PROJECT,
          selection_version: 7,
          monitored_urls: [],
          quota: { used: 0, limit: 50 },
        }),
      ),
      http.get(`/api/v1/site-crawls/${crawl.id}/inventory`, () =>
        HttpResponse.json({ items: [row(URL_A, 'https://acme.com/a'), row(URL_B, 'https://acme.com/b')], next_cursor: null }),
      ),
      http.put(`/api/v1/projects/${PROJECT}/monitored-urls`, async ({ request }) => {
        const json = (await request.json()) as Record<string, unknown>;
        putBody(json);
        return HttpResponse.json({
          project_id: PROJECT,
          selection_version: 8,
          monitored_urls: (json.site_url_ids as string[]).map((id) => ({
            site_url_id: id,
            normalized_url: 'https://acme.com/x',
            display_url: 'https://acme.com/x',
            title: null,
            active: true,
            selection_source: 'user',
            selected_at: '2026-07-16T00:00:00Z',
            deselected_at: null,
          })),
          quota: { used: 1, limit: 50 },
        });
      }),
    );

    renderWithProviders(
      <InventorySelection crawl={crawl} entitlement={entitlement} projectId={PROJECT} />,
    );

    const checkbox = await screen.findByLabelText('Monitor https://acme.com/a');
    await user.click(checkbox);

    const commit = screen.getByRole('button', { name: /analyze 1 of 50 pages/i });
    await user.click(commit);

    await waitFor(() => expect(putBody).toHaveBeenCalledTimes(1));
    expect(putBody.mock.calls[0][0]).toEqual({
      site_url_ids: [URL_A],
      expected_selection_version: 7,
    });
  });

  it('rebases onto the server version and prompts a resubmit on a stale conflict', async () => {
    const user = userEvent.setup();
    let putCount = 0;
    mswServer.use(
      http.get(`/api/v1/projects/${PROJECT}/monitored-urls`, () =>
        HttpResponse.json({
          project_id: PROJECT,
          // Second GET (after the conflict) returns the advanced version.
          selection_version: putCount === 0 ? 3 : 4,
          monitored_urls: [],
          quota: { used: 0, limit: 50 },
        }),
      ),
      http.get(`/api/v1/site-crawls/${crawl.id}/inventory`, () =>
        HttpResponse.json({ items: [row(URL_A, 'https://acme.com/a')], next_cursor: null }),
      ),
      http.put(`/api/v1/projects/${PROJECT}/monitored-urls`, () => {
        putCount += 1;
        return HttpResponse.json(
          { detail: { code: 'stale_selection_version', message: 'stale' } },
          { status: 409 },
        );
      }),
    );

    renderWithProviders(
      <InventorySelection crawl={crawl} entitlement={entitlement} projectId={PROJECT} />,
    );

    await user.click(await screen.findByLabelText('Monitor https://acme.com/a'));
    await user.click(screen.getByRole('button', { name: /analyze 1 of 50 pages/i }));

    // The stale notice appears and the user's edit is preserved for resubmit.
    expect(await screen.findByText(/merged your edits onto the latest version/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /analyze 1 of 50 pages/i })).toBeEnabled();
  });
});
