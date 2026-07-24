import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { ReferralsTable } from './referrals-table';

const PROJECT = '88888888-8888-4888-8888-888888888888';
const REFERRALS_URL = `/api/v1/projects/${PROJECT}/llm-analytics/referrals`;

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: '22222222-2222-4222-8222-222222222222',
    occurred_at: '2026-07-23T20:41:00Z',
    landing_url: 'https://acme-running.example.com/blog/best-trail-running-shoes',
    referrer_host: 'chatgpt.com',
    is_ai_referral: true,
    ai_source: 'chatgpt',
    logical_engine: 'chatgpt',
    confidence: 'exact',
    match_signal: 'referrer',
    ...overrides,
  };
}

const rowA = row();
const rowB = row({
  id: '33333333-3333-4333-8333-333333333333',
  occurred_at: '2026-07-23T19:58:00Z',
  landing_url: 'https://acme-running.example.com/products/acme-apex-gtx',
  referrer_host: 'perplexity.ai',
  ai_source: 'perplexity',
  logical_engine: null,
  confidence: 'heuristic',
  match_signal: 'utm',
});

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('ReferralsTable — rendering', () => {
  it('renders rows with mono url/host cells, source badge, confidence, and signal', async () => {
    mswServer.use(
      http.get(REFERRALS_URL, () => HttpResponse.json({ items: [rowA, rowB], next_cursor: null })),
    );
    renderWithProviders(<ReferralsTable projectId={PROJECT} from={undefined} />);

    expect(await screen.findByText('chatgpt.com')).toBeInTheDocument();
    // Both fixture rows land on the same host (muted host span per row).
    expect(screen.getAllByText('acme-running.example.com')).toHaveLength(2);
    expect(screen.getByText('perplexity.ai')).toBeInTheDocument();
    expect(screen.getByText('ChatGPT')).toBeInTheDocument();
    expect(screen.getByText('Perplexity')).toBeInTheDocument();
    expect(screen.getByText('exact')).toBeInTheDocument();
    expect(screen.getByText('heuristic')).toBeInTheDocument();
    expect(screen.getByText('utm')).toBeInTheDocument();
    expect(screen.getByText('50 rows per page')).toBeInTheDocument();
    // Single page: both pager buttons disabled.
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  it('renders — for a null referrer host and match signal', async () => {
    mswServer.use(
      http.get(REFERRALS_URL, () =>
        HttpResponse.json({
          items: [
            row({
              is_ai_referral: false,
              ai_source: 'other',
              referrer_host: null,
              logical_engine: null,
              match_signal: null,
            }),
          ],
          next_cursor: null,
        }),
      ),
    );
    renderWithProviders(<ReferralsTable projectId={PROJECT} from={undefined} />);

    expect(await screen.findByText('Other')).toBeInTheDocument();
    expect(screen.getAllByText('—')).toHaveLength(2);
  });
});

describe('ReferralsTable — keyset paging', () => {
  it('walks pages with Next/Previous via the cursor envelope (C4)', async () => {
    const seen: string[] = [];
    mswServer.use(
      http.get(REFERRALS_URL, ({ request }) => {
        seen.push(request.url);
        if (new URL(request.url).searchParams.get('cursor') === 'cursor-2') {
          return HttpResponse.json({ items: [rowB], next_cursor: null });
        }
        return HttpResponse.json({ items: [rowA], next_cursor: 'cursor-2' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ReferralsTable projectId={PROJECT} from={undefined} />);

    expect(await screen.findByText('chatgpt.com')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('perplexity.ai')).toBeInTheDocument();
    expect(new URL(seen.at(-1)!).searchParams.get('cursor')).toBe('cursor-2');
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled();

    // Previous pops the stack; page one renders from the cache (no refetch).
    const requestCount = seen.length;
    await user.click(screen.getByRole('button', { name: 'Previous' }));
    expect(await screen.findByText('chatgpt.com')).toBeInTheDocument();
    expect(seen).toHaveLength(requestCount);
  });
});

describe('ReferralsTable — source filter', () => {
  it('sends ?source= and restarts the keyset walk without a cursor', async () => {
    const seen: string[] = [];
    mswServer.use(
      http.get(REFERRALS_URL, ({ request }) => {
        seen.push(request.url);
        const url = new URL(request.url);
        if (url.searchParams.get('source') === 'chatgpt') {
          return HttpResponse.json({ items: [rowA], next_cursor: null });
        }
        if (url.searchParams.get('cursor') === 'cursor-2') {
          return HttpResponse.json({ items: [rowB], next_cursor: null });
        }
        return HttpResponse.json({ items: [rowA], next_cursor: 'cursor-2' });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ReferralsTable projectId={PROJECT} from={undefined} />);

    // Advance to page two first so the stale-cursor case is exercised.
    expect(await screen.findByText('chatgpt.com')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('perplexity.ai')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Filter by source' }));
    await user.click(await screen.findByRole('menuitem', { name: 'ChatGPT' }));

    await waitFor(() => {
      const last = new URL(seen.at(-1)!);
      expect(last.searchParams.get('source')).toBe('chatgpt');
      expect(last.searchParams.get('cursor')).toBeNull();
    });
    // The filter restarts at page one: Previous is disabled again.
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
  });
});

describe('ReferralsTable — empty states', () => {
  it('shows the unfiltered empty note', async () => {
    mswServer.use(
      http.get(REFERRALS_URL, () => HttpResponse.json({ items: [], next_cursor: null })),
    );
    renderWithProviders(<ReferralsTable projectId={PROJECT} from={undefined} />);

    expect(
      await screen.findByText('No AI-referral events recorded in this window yet.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  it('shows the filtered empty note and clears the filter', async () => {
    const seen: string[] = [];
    mswServer.use(
      http.get(REFERRALS_URL, ({ request }) => {
        seen.push(request.url);
        if (new URL(request.url).searchParams.get('source') === 'gemini') {
          return HttpResponse.json({ items: [], next_cursor: null });
        }
        return HttpResponse.json({ items: [rowA], next_cursor: null });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<ReferralsTable projectId={PROJECT} from={undefined} />);

    expect(await screen.findByText('chatgpt.com')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Filter by source' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Gemini' }));

    expect(await screen.findByText('No referral events match Gemini.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Clear source filter' }));
    // The unfiltered page is served from the cache; the chip resets.
    expect(await screen.findByText('chatgpt.com')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Filter by source' })).toHaveTextContent(
      'All sources',
    );
  });
});
