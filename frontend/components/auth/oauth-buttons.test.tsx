import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { assignLocation } from '@/lib/navigate';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { OAuthSection } from './oauth-buttons';

// jsdom 26 defines `Location#assign` (and `window.location` itself) as
// non-configurable, so `vi.spyOn(window.location, 'assign')` throws — the
// component navigates through the `@/lib/navigate` seam, which this suite
// mocks instead (see lib/navigate.ts).
vi.mock('@/lib/navigate', () => ({ assignLocation: vi.fn() }));
const assignMock = vi.mocked(assignLocation);

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  mswServer.resetHandlers();
  assignMock.mockReset();
});
afterAll(() => mswServer.close());

describe('OAuthSection', () => {
  it('renders the three provider buttons and the email divider', () => {
    renderWithProviders(<OAuthSection />);

    expect(screen.getByRole('button', { name: /continue with google/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /continue with github/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /continue with apple/i })).toBeInTheDocument();
    expect(screen.getByText(/or continue with email/i)).toBeInTheDocument();
  });

  it('navigates to the authorize URL on a successful start', async () => {
    const user = userEvent.setup();
    const authorizeUrl = 'https://accounts.example.com/authorize?client_id=abc&state=signed';
    mswServer.use(
      http.get('/api/v1/auth/oauth/google/start', () =>
        HttpResponse.json({ authorize_url: authorizeUrl, state: 'signed' }),
      ),
    );

    renderWithProviders(<OAuthSection />);
    await user.click(screen.getByRole('button', { name: /continue with google/i }));

    await waitFor(() => expect(assignMock).toHaveBeenCalledWith(authorizeUrl));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows a coming-soon info notice naming the provider on a 503', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.get('/api/v1/auth/oauth/github/start', () =>
        HttpResponse.json(
          { detail: { code: 'oauth_provider_not_configured', provider: 'github' } },
          { status: 503 },
        ),
      ),
    );

    renderWithProviders(<OAuthSection />);
    await user.click(screen.getByRole('button', { name: /continue with github/i }));

    expect(await screen.findByText(/github sign-in is coming soon/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(assignMock).not.toHaveBeenCalled();
  });

  it('shows a generic notice on a non-503 error', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.get('/api/v1/auth/oauth/apple/start', () =>
        HttpResponse.json({ detail: 'Upstream failure' }, { status: 500 }),
      ),
    );

    renderWithProviders(<OAuthSection />);
    await user.click(screen.getByRole('button', { name: /continue with apple/i }));

    expect(await screen.findByText(/couldn't start apple sign-in/i)).toBeInTheDocument();
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
    expect(assignMock).not.toHaveBeenCalled();
  });
});
