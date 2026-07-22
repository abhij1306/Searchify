import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

// next/navigation is not available in jsdom — stub the router so we can assert
// on the post-success redirect.
const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}));

import LoginPage from './page';

const sessionUser = {
  id: '11111111-1111-4111-8111-111111111111',
  email: 'user@example.com',
  role: 'owner',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  mswServer.resetHandlers();
  replace.mockReset();
});
afterAll(() => mswServer.close());

describe('LoginPage', () => {
  it('renders the OAuth buttons and the email divider above the form', () => {
    renderWithProviders(<LoginPage />);

    expect(screen.getByRole('button', { name: /continue with google/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /continue with github/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /continue with apple/i })).toBeInTheDocument();
    expect(screen.getByText(/or continue with email/i)).toBeInTheDocument();
  });

  it('shows a coming-soon notice when the OAuth provider is not configured (503)', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.get('/api/v1/auth/oauth/google/start', () =>
        HttpResponse.json(
          { detail: { code: 'oauth_provider_not_configured', provider: 'google' } },
          { status: 503 },
        ),
      ),
    );

    renderWithProviders(<LoginPage />);
    await user.click(screen.getByRole('button', { name: /continue with google/i }));

    expect(await screen.findByText(/google sign-in is coming soon/i)).toBeInTheDocument();
  });

  it('shows validation errors and does not call the API on empty submit', async () => {
    const user = userEvent.setup();
    const loginHandler = vi.fn();
    mswServer.use(
      http.post('/api/v1/auth/login', () => {
        loginHandler();
        return HttpResponse.json({ user: sessionUser });
      }),
    );

    renderWithProviders(<LoginPage />);
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText(/email is required/i)).toBeInTheDocument();
    expect(screen.getByText(/password is required/i)).toBeInTheDocument();
    expect(loginHandler).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it('logs in and redirects to the authed landing on success', async () => {
    const user = userEvent.setup();
    mswServer.use(http.post('/api/v1/auth/login', () => HttpResponse.json({ user: sessionUser })));

    renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), 'user@example.com');
    await user.type(screen.getByLabelText(/password/i), 'sup3rsecret');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'));
  });

  it('surfaces the ApiError message inline on a 401', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.post('/api/v1/auth/login', () =>
        HttpResponse.json({ detail: 'Invalid email or password.' }, { status: 401 }),
      ),
    );

    renderWithProviders(<LoginPage />);
    await user.type(screen.getByLabelText(/email/i), 'user@example.com');
    await user.type(screen.getByLabelText(/password/i), 'wrongpass');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByText(/invalid email or password/i)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
