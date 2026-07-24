import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { PagesTable } from './pages-table';

const PROJECT = '88888888-8888-4888-8888-888888888888';
const PAGES_URL = `/api/v1/projects/${PROJECT}/traffic/pages`;

function pageRow(overrides: Record<string, unknown> = {}) {
  return {
    canonical_url: 'https://acme-running.example.com/blog/best-trail-running-shoes',
    site_url_id: null,
    impressions: 84210,
    clicks: 3204,
    ctr: 0.038,
    position: 4.2,
    sessions: 3102,
    conversions: 88,
    ...overrides,
  };
}

function mockPages(items: Record<string, unknown>[], nextCursor: string | null = null) {
  const seen: URL[] = [];
  mswServer.use(
    http.get(PAGES_URL, ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ items, next_cursor: nextCursor });
    }),
  );
  return seen;
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('PagesTable', () => {
  it('renders rows with mono numerics, the muted-host url cell, and the sort footer note', async () => {
    mockPages([
      pageRow(),
      pageRow({
        canonical_url: 'https://acme-running.example.com/',
        impressions: 44208,
        clicks: 1986,
        ctr: 0.045,
        position: 1.4,
      }),
    ]);
    renderWithProviders(<PagesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('pages-table');
    expect(await within(table).findByText('/blog/best-trail-running-shoes')).toBeInTheDocument();
    expect(within(table).getAllByText('acme-running.example.com')).toHaveLength(2);
    expect(within(table).getByText('84,210')).toBeInTheDocument();
    expect(within(table).getByText('3,204')).toBeInTheDocument();
    expect(within(table).getByText('3.8%')).toBeInTheDocument();
    expect(within(table).getByText('4.2')).toBeInTheDocument();
    expect(within(table).getByText('Sorted by clicks, descending')).toBeInTheDocument();
    // The default sort marks the Clicks column descending.
    const clicksHead = within(table).getByRole('columnheader', { name: 'Clicks' });
    expect(clicksHead).toHaveAttribute('aria-sort', 'descending');
  });

  it('renders null ctr/position as the em-dash placeholder, never a zero', async () => {
    mockPages([pageRow({ ctr: null, position: null, impressions: 1204, clicks: 12 })]);
    renderWithProviders(<PagesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('pages-table');
    await within(table).findByText('1,204');
    expect(within(table).getAllByText('—')).toHaveLength(2);
    expect(within(table).queryByText('0')).not.toBeInTheDocument();
  });

  it('sends the default -clicks sort and toggles columns on header click', async () => {
    const seen = mockPages([pageRow()]);
    const ue = userEvent.setup();
    renderWithProviders(<PagesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('pages-table');
    await within(table).findByText('/blog/best-trail-running-shoes');
    expect(seen[0].searchParams.get('sort')).toBe('-clicks');

    // A new column sorts descending first.
    await ue.click(within(table).getByRole('button', { name: 'Impressions' }));
    await waitFor(() =>
      expect(seen.at(-1)?.searchParams.get('sort')).toBe('-impressions'),
    );
    const impressionsHead = within(table).getByRole('columnheader', { name: 'Impressions' });
    expect(impressionsHead).toHaveAttribute('aria-sort', 'descending');
    expect(within(table).getByText('Sorted by impressions, descending')).toBeInTheDocument();

    // Clicking the active column toggles to ascending.
    await ue.click(within(table).getByRole('button', { name: 'Impressions' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('sort')).toBe('impressions'));
    expect(impressionsHead).toHaveAttribute('aria-sort', 'ascending');
  });

  it('walks keyset pages with Next/Previous and resets the cursor on sort change', async () => {
    const seen: URL[] = [];
    mswServer.use(
      http.get(PAGES_URL, ({ request }) => {
        const url = new URL(request.url);
        seen.push(url);
        const cursor = url.searchParams.get('cursor');
        if (cursor === 'CURSOR-2') {
          return HttpResponse.json({
            items: [pageRow({ canonical_url: 'https://acme-running.example.com/products/apex' })],
            next_cursor: null,
          });
        }
        return HttpResponse.json({ items: [pageRow()], next_cursor: 'CURSOR-2' });
      }),
    );
    const ue = userEvent.setup();
    renderWithProviders(<PagesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('pages-table');
    await within(table).findByText('/blog/best-trail-running-shoes');
    expect(within(table).getByRole('button', { name: 'Previous' })).toBeDisabled();

    await ue.click(within(table).getByRole('button', { name: 'Next' }));
    expect(await within(table).findByText('/products/apex')).toBeInTheDocument();
    expect(seen.some((url) => url.searchParams.get('cursor') === 'CURSOR-2')).toBe(true);
    // Deeper page has no next cursor → Next disables.
    expect(within(table).getByRole('button', { name: 'Next' })).toBeDisabled();

    await ue.click(within(table).getByRole('button', { name: 'Previous' }));
    expect(await within(table).findByText('/blog/best-trail-running-shoes')).toBeInTheDocument();

    // Paging forward again, then changing the sort, drops the cursor (a
    // keyset cursor is bound to its sort fingerprint).
    await ue.click(within(table).getByRole('button', { name: 'Next' }));
    await within(table).findByText('/products/apex');
    await ue.click(within(table).getByRole('button', { name: 'CTR' }));
    await waitFor(() => {
      const last = seen.at(-1);
      expect(last?.searchParams.get('sort')).toBe('-ctr');
      expect(last?.searchParams.get('cursor')).toBeNull();
    });
  });

  it('renders the empty note when the window has no page stats', async () => {
    mockPages([]);
    renderWithProviders(<PagesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('pages-table');
    expect(
      await within(table).findByText('No pages measured for this window.'),
    ).toBeInTheDocument();
  });

  it('forwards the window bounds to the request', async () => {
    const seen = mockPages([pageRow()]);
    renderWithProviders(<PagesTable projectId={PROJECT} from="2026-06-25" to="2026-07-23" />);

    await screen.findByTestId('pages-table');
    await waitFor(() => expect(seen.length).toBeGreaterThan(0));
    expect(seen[0].searchParams.get('from')).toBe('2026-06-25');
    expect(seen[0].searchParams.get('to')).toBe('2026-07-23');
  });
});
