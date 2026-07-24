import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { QueriesTable } from './queries-table';

const PROJECT = '88888888-8888-4888-8888-888888888888';
const QUERIES_URL = `/api/v1/projects/${PROJECT}/traffic/queries`;

function queryRow(overrides: Record<string, unknown> = {}) {
  return {
    normalized_query: 'best trail running shoes 2026',
    impressions: 32104,
    clicks: 1802,
    ctr: 0.056,
    position: 3.1,
    ...overrides,
  };
}

function mockQueries(items: Record<string, unknown>[], nextCursor: string | null = null) {
  const seen: URL[] = [];
  mswServer.use(
    http.get(QUERIES_URL, ({ request }) => {
      seen.push(new URL(request.url));
      return HttpResponse.json({ items, next_cursor: nextCursor });
    }),
  );
  return seen;
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('QueriesTable', () => {
  it('renders rows with mono numerics and the sort footer note', async () => {
    mockQueries([
      queryRow(),
      queryRow({
        normalized_query: 'acme running shoes',
        impressions: 12402,
        clicks: 1588,
        ctr: 0.128,
        position: 1.1,
      }),
    ]);
    renderWithProviders(<QueriesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('queries-table');
    expect(await within(table).findByText('best trail running shoes 2026')).toBeInTheDocument();
    expect(within(table).getByText('acme running shoes')).toBeInTheDocument();
    expect(within(table).getByText('32,104')).toBeInTheDocument();
    expect(within(table).getByText('12.8%')).toBeInTheDocument();
    expect(within(table).getByText('1.1')).toBeInTheDocument();
    expect(within(table).getByText('Sorted by clicks, descending')).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: 'Clicks' })).toHaveAttribute(
      'aria-sort',
      'descending',
    );
  });

  it('renders null position as the em-dash placeholder', async () => {
    mockQueries([queryRow({ normalized_query: 'acme velocity 2 vs apex', position: null })]);
    renderWithProviders(<QueriesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('queries-table');
    await within(table).findByText('acme velocity 2 vs apex');
    expect(within(table).getByText('—')).toBeInTheDocument();
  });

  it('sends the default -clicks sort and toggles columns on header click', async () => {
    const seen = mockQueries([queryRow()]);
    const ue = userEvent.setup();
    renderWithProviders(<QueriesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('queries-table');
    await within(table).findByText('best trail running shoes 2026');
    expect(seen[0].searchParams.get('sort')).toBe('-clicks');

    await ue.click(within(table).getByRole('button', { name: 'Position' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('sort')).toBe('-position'));
    expect(within(table).getByRole('columnheader', { name: 'Position' })).toHaveAttribute(
      'aria-sort',
      'descending',
    );

    await ue.click(within(table).getByRole('button', { name: 'Position' }));
    await waitFor(() => expect(seen.at(-1)?.searchParams.get('sort')).toBe('position'));
    expect(within(table).getByText('Sorted by position, ascending')).toBeInTheDocument();
  });

  it('walks keyset pages with Next/Previous', async () => {
    mswServer.use(
      http.get(QUERIES_URL, ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor');
        if (cursor === 'CURSOR-2') {
          return HttpResponse.json({
            items: [queryRow({ normalized_query: 'marathon training plan beginner' })],
            next_cursor: null,
          });
        }
        return HttpResponse.json({ items: [queryRow()], next_cursor: 'CURSOR-2' });
      }),
    );
    const ue = userEvent.setup();
    renderWithProviders(<QueriesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('queries-table');
    await within(table).findByText('best trail running shoes 2026');

    await ue.click(within(table).getByRole('button', { name: 'Next' }));
    expect(await within(table).findByText('marathon training plan beginner')).toBeInTheDocument();
    expect(within(table).queryByText('best trail running shoes 2026')).not.toBeInTheDocument();

    await ue.click(within(table).getByRole('button', { name: 'Previous' }));
    expect(await within(table).findByText('best trail running shoes 2026')).toBeInTheDocument();
  });

  it('renders the empty note when the window has no query stats', async () => {
    mockQueries([]);
    renderWithProviders(<QueriesTable projectId={PROJECT} />);

    const table = await screen.findByTestId('queries-table');
    expect(
      await within(table).findByText('No queries measured for this window.'),
    ).toBeInTheDocument();
  });
});
