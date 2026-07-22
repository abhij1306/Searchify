import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { useState } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { ConnectProviderDialog } from './connect-provider-dialog';

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

const CONNECTION_ID = '11111111-1111-4111-8111-111111111111';
const WORKSPACE_ID = '22222222-2222-4222-8222-222222222222';

// v2 direct-provider retirement: one direct transport per logical engine.
const catalog = {
  transports: ['openai', 'anthropic', 'google'],
  engines: [
    {
      logical_engine: 'chatgpt',
      routes: [{ transport_provider: 'openai', default_model: 'gpt-5.4' }],
    },
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

/** Stateful host so the dialog really closes after a successful save. */
function Harness({ onConnected }: { onConnected?: () => void }) {
  const [open, setOpen] = useState(true);
  return <ConnectProviderDialog open={open} onOpenChange={setOpen} onConnected={onConnected} />;
}

async function findDialog() {
  return screen.findByRole('dialog', { name: 'Connect a provider' });
}

describe('ConnectProviderDialog', () => {
  it('saves a key for the picked engine, then closes and refreshes the connections query', async () => {
    const user = userEvent.setup();
    const onConnected = vi.fn();
    let createdBody: Record<string, unknown> | null = null;
    let connectionReads = 0;
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => {
        connectionReads += 1;
        return HttpResponse.json([]);
      }),
      http.post('/api/v1/provider-connections', async ({ request }) => {
        createdBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          connection({
            transport_provider: 'anthropic',
            routes: [
              {
                id: '44444444-4444-4444-8444-444444444444',
                logical_engine: 'claude',
                transport_provider: 'anthropic',
                transport_model: 'claude-sonnet-4-6',
                is_default: false,
                active: true,
              },
            ],
          }),
          { status: 201 },
        );
      }),
    );

    renderWithProviders(<Harness onConnected={onConnected} />);
    const dialog = await findDialog();

    // Engine picker lists the three logical engines and defaults to the first
    // unconfigured one (none configured yet → ChatGPT).
    const picker = within(dialog).getByLabelText('AI engine');
    expect(within(picker).getByRole('option', { name: 'ChatGPT' })).toBeInTheDocument();
    expect(within(picker).getByRole('option', { name: 'Gemini' })).toBeInTheDocument();
    expect(within(picker).getByRole('option', { name: 'Claude' })).toBeInTheDocument();
    expect(picker).toHaveValue('chatgpt');

    await user.selectOptions(picker, 'claude');
    // The picked engine's direct route is shown before connecting.
    expect(await within(dialog).findByText(/claude-sonnet-4-6/)).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText(/api key/i), 'sk-ant-key');
    await user.click(within(dialog).getByRole('button', { name: /save key/i }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(createdBody).toEqual({
      transport_provider: 'anthropic',
      api_key: 'sk-ant-key',
      routes: [
        { logical_engine: 'claude', transport_model: 'claude-sonnet-4-6', is_default: false },
      ],
    });
    expect(onConnected).toHaveBeenCalledTimes(1);
    // Save invalidated the shared connections query → it refetched.
    await waitFor(() => expect(connectionReads).toBeGreaterThanOrEqual(2));
  });

  it('tests the existing connection inline, then updates the key and closes', async () => {
    const user = userEvent.setup();
    let patchBody: Record<string, unknown> | null = null;
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([connection()])),
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
      http.patch(`/api/v1/provider-connections/${CONNECTION_ID}`, async ({ request }) => {
        patchBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(connection());
      }),
    );

    renderWithProviders(<Harness />);
    const dialog = await findDialog();

    // ChatGPT is already configured → once the connections query lands the
    // picker defaults to the first engine that still needs a key.
    const picker = within(dialog).getByLabelText('AI engine');
    await waitFor(() => expect(picker).toHaveValue('gemini'));
    await user.selectOptions(picker, 'chatgpt');

    const keyInput = within(dialog).getByLabelText(/api key/i);
    await user.type(keyInput, 'sk-rotated');

    await user.click(within(dialog).getByRole('button', { name: /test connection/i }));
    expect(
      await within(dialog).findByText(/connection succeeded \(gpt-5\.4\)\./i),
    ).toBeInTheDocument();

    await user.click(within(dialog).getByRole('button', { name: /update key/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(patchBody).toMatchObject({ api_key: 'sk-rotated' });
  });

  it('shows the failure alert when the connection test fails', async () => {
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

    renderWithProviders(<Harness />);
    const dialog = await findDialog();
    await user.selectOptions(within(dialog).getByLabelText('AI engine'), 'chatgpt');

    await user.click(within(dialog).getByRole('button', { name: /test connection/i }));
    expect(await within(dialog).findByText('Invalid API key')).toBeInTheDocument();
    // A failed test does not close the dialog.
    expect(screen.getByRole('dialog', { name: 'Connect a provider' })).toBeInTheDocument();
  });

  it('shows a save error and stays open', async () => {
    const user = userEvent.setup();
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([])),
      http.post('/api/v1/provider-connections', () =>
        HttpResponse.json({ detail: 'Provider rejected the key' }, { status: 400 }),
      ),
    );

    renderWithProviders(<Harness />);
    const dialog = await findDialog();
    await user.type(within(dialog).getByLabelText(/api key/i), 'sk-bad-key');
    await user.click(within(dialog).getByRole('button', { name: /save key/i }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Provider rejected the key');
    expect(screen.getByRole('dialog', { name: 'Connect a provider' })).toBeInTheDocument();
  });

  it('never pre-fills a stored key — the input stays empty and write-only', async () => {
    const user = userEvent.setup();
    mswServer.use(
      catalogHandler(),
      http.get('/api/v1/provider-connections', () => HttpResponse.json([connection()])),
    );

    renderWithProviders(<Harness />);
    const dialog = await findDialog();
    await user.selectOptions(within(dialog).getByLabelText('AI engine'), 'chatgpt');

    const keyInput = within(dialog).getByLabelText(/api key/i);
    expect(keyInput).toHaveAttribute('type', 'password');
    expect(keyInput).toHaveValue('');
    expect(keyInput).toHaveAttribute('placeholder', '•••••••• stored');
  });
});
