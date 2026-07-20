import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';

import { createAppQueryClient } from '@/lib/api/query-client';
import { queryKeys } from '@/lib/api/query-keys';
import { mswServer } from '@/test/msw-server';

import { isTerminalContentStatus, useContentGenerations } from './use-content-generations';

const PROJECT = '22222222-2222-4222-8222-222222222222';
const GEN = '11111111-1111-4111-8111-111111111111';

const listItem = {
  id: GEN,
  project_id: PROJECT,
  status: 'queued',
  output_type: 'website_page',
  website_context_status: 'included',
  requested_model: 'mistral-small-latest',
  returned_model: null,
  provider: 'mistral',
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
  completed_at: null,
  error_code: '',
  prompt_preview: 'Write a landing page',
};

const detail = {
  ...listItem,
  prompt: 'Write a landing page for Acme.',
  website_context_enabled: true,
  website_context_summary: null,
  finish_reason: null,
  output_truncated: false,
  output_text: null,
  usage: null,
  latency_ms: null,
  error_detail: '',
  generator_version: 'content-v1',
};

function setup() {
  const queryClient = createAppQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useContentGenerations(PROJECT), { wrapper });
  return { queryClient, ...hook };
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('isTerminalContentStatus', () => {
  it('is true only for succeeded/failed/cancelled', () => {
    expect(isTerminalContentStatus('succeeded')).toBe(true);
    expect(isTerminalContentStatus('failed')).toBe(true);
    expect(isTerminalContentStatus('cancelled')).toBe(true);
    expect(isTerminalContentStatus('queued')).toBe(false);
    expect(isTerminalContentStatus('leased')).toBe(false);
    expect(isTerminalContentStatus('running')).toBe(false);
    expect(isTerminalContentStatus('retry_wait')).toBe(false);
  });
});

describe('useContentGenerations', () => {
  it('loads the history list for the project', async () => {
    mswServer.use(
      http.get('/api/v1/content/generations', () => HttpResponse.json([listItem])),
    );
    const { result } = setup();
    await waitFor(() => expect(result.current.listQuery.data).toHaveLength(1));
    expect(result.current.listQuery.data?.[0].id).toBe(GEN);
  });

  it('enqueue selects the new record, primes the detail cache, and invalidates the list', async () => {
    let listCalls = 0;
    mswServer.use(
      http.get('/api/v1/content/generations', () => {
        listCalls += 1;
        return HttpResponse.json(listCalls > 1 ? [listItem] : []);
      }),
      http.post('/api/v1/content/generations', () =>
        HttpResponse.json(detail, { status: 201 }),
      ),
      // The primed detail is non-terminal, so the detail query still polls the
      // endpoint; serve it to keep onUnhandledRequest: 'error' quiet.
      http.get(`/api/v1/content/generations/${GEN}`, () => HttpResponse.json(detail)),
    );
    const { result, queryClient } = setup();
    await waitFor(() => expect(result.current.listQuery.isSuccess).toBe(true));

    act(() => {
      result.current.enqueueMutation.mutate({
        prompt: 'Write a landing page for Acme.',
        websiteContextEnabled: true,
      });
    });
    await waitFor(() => expect(result.current.enqueueMutation.isSuccess).toBe(true));

    expect(result.current.selectedId).toBe(GEN);
    expect(queryClient.getQueryData(queryKeys.content.detail(GEN))).toMatchObject({ id: GEN });
    await waitFor(() => expect(listCalls).toBeGreaterThan(1));
  });

  it('cancel updates the detail cache to the cancelled record', async () => {
    let cancelled = false;
    const cancelledDetail = {
      ...detail,
      status: 'cancelled',
      error_code: 'cancelled',
      completed_at: '2026-07-15T00:01:00Z',
    };
    mswServer.use(
      http.get('/api/v1/content/generations', () => HttpResponse.json([listItem])),
      http.get(`/api/v1/content/generations/${GEN}`, () =>
        HttpResponse.json(cancelled ? cancelledDetail : detail),
      ),
      http.post(`/api/v1/content/generations/${GEN}/cancel`, () => {
        cancelled = true;
        return HttpResponse.json(cancelledDetail);
      }),
    );
    const { result, queryClient } = setup();
    act(() => result.current.setSelectedId(GEN));
    await waitFor(() => expect(result.current.detailQuery.isSuccess).toBe(true));

    act(() => result.current.cancelMutation.mutate(GEN));
    await waitFor(() => expect(result.current.cancelMutation.isSuccess).toBe(true));
    await waitFor(() =>
      expect(queryClient.getQueryData(queryKeys.content.detail(GEN))).toMatchObject({
        status: 'cancelled',
      }),
    );
  });

  it('surfaces enqueue errors (409 provider not configured)', async () => {
    mswServer.use(
      http.get('/api/v1/content/generations', () => HttpResponse.json([])),
      http.post('/api/v1/content/generations', () =>
        HttpResponse.json({ detail: 'provider_not_configured' }, { status: 409 }),
      ),
    );
    const { result } = setup();
    act(() => {
      result.current.enqueueMutation.mutate({ prompt: 'p', websiteContextEnabled: true });
    });
    await waitFor(() => expect(result.current.enqueueMutation.isError).toBe(true));
  });
});
