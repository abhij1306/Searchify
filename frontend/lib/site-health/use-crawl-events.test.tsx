import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { setActiveWorkspaceId } from '@/lib/api/client';
import { queryKeys } from '@/lib/api/query-keys';
import { useCrawlEvents } from './use-crawl-events';

const CRAWL = '11111111-1111-4111-8111-111111111111';
const PROJECT = '22222222-2222-4222-8222-222222222222';

function makeStreamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: true, body } as unknown as Response;
}

function wrapper(client: QueryClient) {
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  }
  return Wrapper;
}

afterEach(() => {
  vi.restoreAllMocks();
  setActiveWorkspaceId(null);
});

describe('useCrawlEvents', () => {
  it('sends X-Workspace-Id and credentials, and invalidates on a data frame', async () => {
    setActiveWorkspaceId('99999999-9999-4999-8999-999999999999');
    const fetchMock = vi
      .fn()
      .mockResolvedValue(makeStreamResponse(['data: {"event_type":"page_updated"}\n\n']));
    vi.stubGlobal('fetch', fetchMock);

    const client = new QueryClient();
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');

    renderHook(() => useCrawlEvents(CRAWL, PROJECT, true), { wrapper: wrapper(client) });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0];
    expect(init.credentials).toBe('include');
    expect(init.headers['X-Workspace-Id']).toBe('99999999-9999-4999-8999-999999999999');

    // A data frame invalidates the crawl + pages queries (progress accelerator).
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: queryKeys.siteHealth.pages(CRAWL),
      }),
    );
    vi.unstubAllGlobals();
  });

  it('does not open a stream when disabled', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const client = new QueryClient();
    renderHook(() => useCrawlEvents(CRAWL, PROJECT, false), { wrapper: wrapper(client) });
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('swallows a failed stream so polling is never blocked', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('network'));
    vi.stubGlobal('fetch', fetchMock);
    const client = new QueryClient();
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries');

    renderHook(() => useCrawlEvents(CRAWL, PROJECT, true), { wrapper: wrapper(client) });

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // No throw, no invalidation from a dead stream.
    expect(invalidateSpy).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
