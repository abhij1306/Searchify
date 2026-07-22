import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { createAppQueryClient } from '@/lib/api/query-client';
import { queryKeys } from '@/lib/api/query-keys';
import { mswServer } from '@/test/msw-server';

import { useAuthMutation } from './use-auth-mutation';

// next/navigation is not available in jsdom — stub the router so we can assert
// on the post-success redirect (mirrors the auth page tests).
const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}));

const sessionUser = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'user@example.com',
  role: 'owner',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const project = {
  id: '22222222-2222-4222-8222-222222222222',
  workspace_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  name: 'Acme',
  brand_name: 'Acme',
  website_url: 'https://example.com',
  country_code: 'US',
  language_code: 'en',
  benchmark_mode: 'consumer_like',
  default_repetitions: 3,
  brand: { aliases: [] },
  owned_domains: [],
  unintended_domains: [],
  competitors: [],
  prompt_sets: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function setup() {
  const queryClient = createAppQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  // The auth call itself is stubbed to resolve immediately — routing is driven
  // by the mocked `/projects` response.
  const hook = renderHook(() => useAuthMutation(() => Promise.resolve(sessionUser)), { wrapper });
  return { queryClient, ...hook };
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  mswServer.resetHandlers();
  replace.mockReset();
});
afterAll(() => mswServer.close());

describe('useAuthMutation', () => {
  it('primes the me cache and routes to /setup when the workspace has no projects', async () => {
    mswServer.use(http.get('/api/v1/projects', () => HttpResponse.json([])));
    const { result, queryClient } = setup();

    act(() => {
      void result.current.submit({});
    });

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/setup'));
    expect(queryClient.getQueryData(queryKeys.auth.me())).toMatchObject({ id: sessionUser.id });
  });

  it('routes to /visibility when the workspace already has a project', async () => {
    mswServer.use(http.get('/api/v1/projects', () => HttpResponse.json([project])));
    const { result } = setup();

    act(() => {
      void result.current.submit({});
    });

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/visibility'));
  });

  it('falls back to /setup when the projects lookup fails', async () => {
    // 4xx: the shared retry policy never retries it, so the fallback is
    // immediate.
    mswServer.use(
      http.get('/api/v1/projects', () =>
        HttpResponse.json({ detail: 'forbidden' }, { status: 403 }),
      ),
    );
    const { result } = setup();

    act(() => {
      void result.current.submit({});
    });

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/setup'));
    expect(result.current.mutation.isError).toBe(false);
  });

  it('stays pending until the projects lookup settles and the redirect fires', async () => {
    let respond: ((response: Response) => void) | undefined;
    mswServer.use(
      http.get(
        '/api/v1/projects',
        () =>
          new Promise<Response>((resolve) => {
            respond = resolve;
          }),
      ),
    );
    const { result } = setup();

    act(() => {
      void result.current.submit({});
    });

    await waitFor(() => expect(result.current.mutation.isPending).toBe(true));
    expect(replace).not.toHaveBeenCalled();

    // isPending flips before the MSW handler has necessarily assigned
    // `respond` — wait for the request to actually arrive before resolving.
    await waitFor(() => expect(respond).toBeTypeOf('function'));
    if (!respond) throw new Error('Projects request did not start');
    respond(HttpResponse.json([]));
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/setup'));
    await waitFor(() => expect(result.current.mutation.isPending).toBe(false));
  });
});
