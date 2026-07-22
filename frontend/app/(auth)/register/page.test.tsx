import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

// next/navigation is not available in jsdom — stub the router so we can assert
// on the post-success redirect (mirrors the login page test).
const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}));

import RegisterPage from './page';

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

describe('RegisterPage', () => {
  it('renders the OAuth buttons and the email divider above the form', () => {
    renderWithProviders(<RegisterPage />);

    expect(screen.getByRole('button', { name: /continue with google/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /continue with github/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /continue with apple/i })).toBeInTheDocument();
    expect(screen.getByText(/or continue with email/i)).toBeInTheDocument();
  });

  it('shows validation errors and does not call the API on empty submit', async () => {
    const user = userEvent.setup();
    const registerHandler = vi.fn();
    mswServer.use(
      http.post('/api/v1/auth/register', () => {
        registerHandler();
        return HttpResponse.json({ user: sessionUser });
      }),
    );

    renderWithProviders(<RegisterPage />);
    await user.click(screen.getByRole('button', { name: /create account/i }));

    expect(await screen.findByText(/email is required/i)).toBeInTheDocument();
    expect(screen.getByText(/password must be at least 8 characters/i)).toBeInTheDocument();
    expect(screen.getByText(/confirm your password/i)).toBeInTheDocument();
    expect(registerHandler).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});
