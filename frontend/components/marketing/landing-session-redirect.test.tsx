import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';

import { queryKeys } from '@/lib/api/query-keys';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}));

import { LandingSessionRedirect } from './landing-session-redirect';

const sessionUser = {
  id: '22222222-2222-4222-8222-222222222222',
  email: 'landing@example.com',
  role: 'owner',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const WORKSPACE_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT_1 = '11111111-1111-4111-8111-111111111111';

function project(id: string, name: string) {
  return {
    id,
    workspace_id: WORKSPACE_A,
    name,
    brand_name: name,
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
}

/** The island next to a marker standing in for the public page content. */
function LandingWithMarker() {
  return (
    <>
      <LandingSessionRedirect />
      <div>marketing page content</div>
    </>
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  mswServer.resetHandlers();
  replace.mockReset();
});
afterAll(() => mswServer.close());

describe('LandingSessionRedirect', () => {
  it('redirects an authed visitor with a project to /visibility', async () => {
    mswServer.use(
      http.get('/api/v1/auth/me', () => HttpResponse.json({ user: sessionUser })),
      http.get('/api/v1/projects', () => HttpResponse.json([project(PROJECT_1, 'Acme')])),
    );

    renderWithProviders(<LandingWithMarker />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/visibility'));
  });

  it('redirects an authed visitor with no projects to /setup', async () => {
    mswServer.use(
      http.get('/api/v1/auth/me', () => HttpResponse.json({ user: sessionUser })),
      http.get('/api/v1/projects', () => HttpResponse.json([])),
    );

    renderWithProviders(<LandingWithMarker />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/setup'));
  });

  it('stays put for an anonymous visitor (me 401) and never gates page content', async () => {
    mswServer.use(
      http.get('/api/v1/auth/me', () =>
        HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
      ),
    );

    const { queryClient } = renderWithProviders(<LandingWithMarker />);

    // The island renders null — the marketing content is on screen from the
    // first paint, never blocked on the session check.
    expect(screen.getByText('marketing page content')).toBeInTheDocument();

    // Let the 401 settle; no redirect may fire for an anonymous visitor.
    await waitFor(() =>
      expect(queryClient.getQueryState(queryKeys.auth.me())?.status).toBe('error'),
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    expect(replace).not.toHaveBeenCalled();
    expect(screen.getByText('marketing page content')).toBeInTheDocument();
  });

  it('stays put when me fails with a non-401 error', async () => {
    mswServer.use(
      http.get('/api/v1/auth/me', () =>
        HttpResponse.json({ detail: 'Forbidden' }, { status: 403 }),
      ),
    );

    const { queryClient } = renderWithProviders(<LandingWithMarker />);

    await waitFor(() =>
      expect(queryClient.getQueryState(queryKeys.auth.me())?.status).toBe('error'),
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    expect(replace).not.toHaveBeenCalled();
  });

  it('stays put when the projects query fails (never dumps an authed user into /setup)', async () => {
    mswServer.use(
      http.get('/api/v1/auth/me', () => HttpResponse.json({ user: sessionUser })),
      http.get('/api/v1/projects', () =>
        HttpResponse.json({ detail: 'Forbidden' }, { status: 403 }),
      ),
    );

    const { queryClient } = renderWithProviders(<LandingWithMarker />);

    await waitFor(() =>
      expect(queryClient.getQueryState(queryKeys.projects.list())?.status).toBe('error'),
    );
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    expect(replace).not.toHaveBeenCalled();
  });
});
