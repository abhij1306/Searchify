import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import ProvidersPage from './page';

const CONNECTION_ID = '11111111-1111-4111-8111-111111111111';
const WORKSPACE_ID = '22222222-2222-4222-8222-222222222222';

const catalog = {
  transports: ['anthropic', 'google', 'openrouter'],
  engines: [
    { logical_engine: 'chatgpt', routes: [{ transport_provider: 'openrouter', default_model: 'openai/gpt-5.4' }] },
    {
      logical_engine: 'gemini',
      routes: [
        { transport_provider: 'google', default_model: 'gemini-flash-latest' },
        { transport_provider: 'openrouter', default_model: 'google/gemini-2.5-flash' },
      ],
    },
    {
      logical_engine: 'claude',
      routes: [
        { transport_provider: 'anthropic', default_model: 'claude-sonnet-4-6' },
        { transport_provider: 'openrouter', default_model: 'anthropic/claude-sonnet-4.6' },
      ],
    },
  ],
};

function connection(overrides: Record<string, unknown> = {}) {
  return {
    id: CONNECTION_ID,
    workspace_id: WORKSPACE_ID,
    label: 'gemini',
    transport_provider: 'google',
    base_url: null,
    active: true,
    api_key_set: true,
    last_tested_at: null,
    last_test_status: '',
    routes: [
      {
        id: '33333333-3333-4333-8333-333333333333',
        logical_engine: 'gemini',
        transport_provider: 'google',
        transport_model: 'gemini-flash-latest',
        is_default: false,
      },
    ],
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
    ...overrides,
  };
}

function catalogHandler() {
  return http.get('/api/v1/provider-catalog', () => HttpResponse.json(catalog));
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => window.localStorage.clear());
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('ProvidersPage', () => {
  it('renders a card for all three engines with unconfigured state', async () => {
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([])),
    );

    renderWithProviders(<ProvidersPage />);

    expect(await screen.findByRole('heading', { name: 'ChatGPT' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Gemini' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Claude' })).toBeInTheDocument();
    // No connections → every card reads "Not configured".
    expect(screen.getAllByText('Not configured')).toHaveLength(3);
  });

  it('shows ChatGPT as OpenRouter-only with the direct OpenAI option disabled', async () => {
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([])),
    );

    renderWithProviders(<ProvidersPage />);

    const chatgptCard = (await screen.findByRole('heading', { name: 'ChatGPT' })).closest('section')!;
    const utils = within(chatgptCard);
    const openrouter = utils.getByRole('radio', { name: /OpenRouter/i });
    expect(openrouter).toHaveAttribute('aria-checked', 'true');
    const directOpenai = utils.getByRole('radio', { name: /Direct OpenAI/i });
    expect(directOpenai).toBeDisabled();
    expect(directOpenai).toHaveTextContent(/coming soon/i);
  });

  it('lets the Gemini route toggle switch transports', async () => {
    const user = userEvent.setup();
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([])),
    );

    renderWithProviders(<ProvidersPage />);

    const geminiCard = (await screen.findByRole('heading', { name: 'Gemini' })).closest('section')!;
    const utils = within(geminiCard);
    const direct = utils.getByRole('radio', { name: /Direct \(Google\)/i });
    const openrouter = utils.getByRole('radio', { name: /OpenRouter/i });
    expect(direct).toHaveAttribute('aria-checked', 'true');

    await user.click(openrouter);
    expect(openrouter).toHaveAttribute('aria-checked', 'true');
    expect(direct).toHaveAttribute('aria-checked', 'false');
  });

  it('submits a new key and surfaces a successful connection test', async () => {
    const user = userEvent.setup();
    let created = false;
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () =>
        HttpResponse.json(created ? [connection()] : []),
      ),
      http.post('/api/v1/provider-connections', async () => {
        created = true;
        return HttpResponse.json(connection(), { status: 201 });
      }),
      http.post(`/api/v1/provider-connections/${CONNECTION_ID}/test`, () =>
        HttpResponse.json({
          connection_id: CONNECTION_ID,
          status: 'ok',
          error_code: '',
          detail: 'Connection succeeded',
          latency_ms: 42,
          logical_engine: 'gemini',
          transport_provider: 'google',
          transport_model: 'gemini-flash-latest',
          tested_at: '2026-07-15T00:00:00Z',
        }),
      ),
    );

    renderWithProviders(<ProvidersPage />);

    const geminiCard = (await screen.findByRole('heading', { name: 'Gemini' })).closest('section')!;
    const utils = within(geminiCard);

    await user.type(utils.getByPlaceholderText(/paste your api key/i), 'sk-test-key');
    await user.click(utils.getByRole('button', { name: /save key/i }));

    // After save the connection list refetches → card becomes configured.
    await waitFor(() => expect(utils.getByText('Configured')).toBeInTheDocument());

    await user.click(utils.getByRole('button', { name: /test connection/i }));
    expect(await utils.findByText(/connection succeeded/i)).toBeInTheDocument();
  });

  it('surfaces a failed connection test', async () => {
    const user = userEvent.setup();
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([connection()])),
      http.post(`/api/v1/provider-connections/${CONNECTION_ID}/test`, () =>
        HttpResponse.json({
          connection_id: CONNECTION_ID,
          status: 'failed',
          error_code: 'auth_failure',
          detail: 'Invalid API key',
          latency_ms: 10,
          logical_engine: 'gemini',
          transport_provider: 'google',
          transport_model: 'gemini-flash-latest',
          tested_at: '2026-07-15T00:00:00Z',
        }),
      ),
    );

    renderWithProviders(<ProvidersPage />);

    const geminiCard = (await screen.findByRole('heading', { name: 'Gemini' })).closest('section')!;
    const utils = within(geminiCard);
    expect(utils.getByText('Configured')).toBeInTheDocument();

    await user.click(utils.getByRole('button', { name: /test connection/i }));
    expect(await utils.findByText(/invalid api key/i)).toBeInTheDocument();
  });

  it('never renders the stored secret — key input is empty and write-only', async () => {
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([connection()])),
    );

    renderWithProviders(<ProvidersPage />);

    const geminiCard = (await screen.findByRole('heading', { name: 'Gemini' })).closest('section')!;
    const utils = within(geminiCard);
    const keyInput = utils.getByPlaceholderText(/stored/i) as HTMLInputElement;
    expect(keyInput).toHaveAttribute('type', 'password');
    expect(keyInput.value).toBe('');
  });
});
