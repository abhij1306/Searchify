import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import ProvidersPage from './page';

const CONNECTION_ID = '11111111-1111-4111-8111-111111111111';
const WORKSPACE_ID = '22222222-2222-4222-8222-222222222222';

// v2 direct-provider retirement: one direct transport per logical engine.
const catalog = {
  transports: ['openai', 'anthropic', 'google'],
  engines: [
    { logical_engine: 'chatgpt', routes: [{ transport_provider: 'openai', default_model: 'gpt-5.4' }] },
    {
      logical_engine: 'gemini',
      routes: [{ transport_provider: 'google', default_model: 'gemini-flash-latest' }],
    },
    {
      logical_engine: 'claude',
      routes: [{ transport_provider: 'anthropic', default_model: 'claude-sonnet-4-6' }],
    },
  ],
};

function connection(overrides: Record<string, unknown> = {}) {
  return {
    id: CONNECTION_ID,
    workspace_id: WORKSPACE_ID,
    label: 'chatgpt',
    transport_provider: 'openai',
    base_url: null,
    active: true,
    api_key_set: true,
    last_tested_at: null,
    last_test_status: '',
    routes: [
      {
        id: '33333333-3333-4333-8333-333333333333',
        logical_engine: 'chatgpt',
        transport_provider: 'openai',
        transport_model: 'gpt-5.4',
        is_default: false,
        active: true,
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

  it('shows ChatGPT as a fixed direct OpenAI route with no OpenRouter and no toggle', async () => {
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([])),
    );

    renderWithProviders(<ProvidersPage />);

    const chatgptCard = (await screen.findByRole('heading', { name: 'ChatGPT' })).closest('section')!;
    const utils = within(chatgptCard);
    // Fixed direct route label; the OpenAI model is surfaced.
    expect(utils.getByText('Direct (OpenAI)')).toBeInTheDocument();
    expect(utils.getByText(/gpt-5\.4/)).toBeInTheDocument();
    // No route toggle / radios at all, and no OpenRouter or "coming soon" copy.
    expect(utils.queryByRole('radio')).toBeNull();
    expect(utils.queryByText(/openrouter/i)).toBeNull();
    expect(utils.queryByText(/coming soon/i)).toBeNull();
  });

  it('never renders an OpenRouter control anywhere on the page', async () => {
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([])),
    );

    renderWithProviders(<ProvidersPage />);
    await screen.findByRole('heading', { name: 'ChatGPT' });
    expect(screen.queryByText(/openrouter/i)).toBeNull();
  });

  it('submits a new OpenAI key and surfaces a successful connection test', async () => {
    const user = userEvent.setup();
    let created = false;
    let createdTransport = '';
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () =>
        HttpResponse.json(created ? [connection()] : []),
      ),
      http.post('/api/v1/provider-connections', async ({ request }) => {
        const body = (await request.json()) as { transport_provider: string };
        createdTransport = body.transport_provider;
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
          logical_engine: 'chatgpt',
          transport_provider: 'openai',
          transport_model: 'gpt-5.4',
          tested_at: '2026-07-15T00:00:00Z',
        }),
      ),
    );

    renderWithProviders(<ProvidersPage />);

    const chatgptCard = (await screen.findByRole('heading', { name: 'ChatGPT' })).closest('section')!;
    const utils = within(chatgptCard);

    await user.type(utils.getByPlaceholderText(/paste your api key/i), 'sk-test-key');
    await user.click(utils.getByRole('button', { name: /save key/i }));

    // After save the connection list refetches → card becomes configured.
    await waitFor(() => expect(utils.getByText('Configured')).toBeInTheDocument());
    expect(createdTransport).toBe('openai');

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
          logical_engine: 'chatgpt',
          transport_provider: 'openai',
          transport_model: 'gpt-5.4',
          tested_at: '2026-07-15T00:00:00Z',
        }),
      ),
    );

    renderWithProviders(<ProvidersPage />);

    const chatgptCard = (await screen.findByRole('heading', { name: 'ChatGPT' })).closest('section')!;
    const utils = within(chatgptCard);
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

    const chatgptCard = (await screen.findByRole('heading', { name: 'ChatGPT' })).closest('section')!;
    const utils = within(chatgptCard);
    const keyInput = utils.getByPlaceholderText(/stored/i) as HTMLInputElement;
    expect(keyInput).toHaveAttribute('type', 'password');
    expect(keyInput.value).toBe('');
  });
});
