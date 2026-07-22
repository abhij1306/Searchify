import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { createElement, type ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { createAppQueryClient } from '@/lib/api/query-client';
import { queryKeys } from '@/lib/api/query-keys';
import type { ProviderConnection } from '@/lib/api/types';
import { mswServer } from '@/test/msw-server';

import type { EngineCardModel } from './catalog';
import { useEngineConnection } from './use-engine-connection';

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

const CONNECTION_ID = '11111111-1111-4111-8111-111111111111';
const WORKSPACE_ID = '22222222-2222-4222-8222-222222222222';

// ChatGPT is served by the single fixed direct OpenAI route (v2 retirement).
const chatgptModel: EngineCardModel = {
  logical_engine: 'chatgpt',
  label: 'ChatGPT',
  route: { transport_provider: 'openai', default_model: 'gpt-5.4', label: 'Direct (OpenAI)' },
};

function connection(overrides: Partial<ProviderConnection> = {}): ProviderConnection {
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

function testResultPayload(overrides: Record<string, unknown> = {}) {
  return {
    connection_id: CONNECTION_ID,
    status: 'ok',
    error_code: '',
    detail: 'Connection succeeded',
    latency_ms: 42,
    logical_engine: 'chatgpt',
    transport_provider: 'openai',
    transport_model: 'gpt-5.4',
    tested_at: '2026-07-15T00:00:00Z',
    ...overrides,
  };
}

function setup(
  model: EngineCardModel,
  connections: ProviderConnection[] = [],
  onSaved?: () => void,
) {
  const queryClient = createAppQueryClient();
  // Seed the shared connections query so save-time invalidation is observable.
  queryClient.setQueryData(queryKeys.providers.connections(), connections);
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
  return {
    queryClient,
    ...renderHook(() => useEngineConnection({ model, connections, onSaved }), { wrapper }),
  };
}

describe('useEngineConnection', () => {
  it('creates the direct-transport connection on save, then clears the key and invalidates connections', async () => {
    let body: Record<string, unknown> | null = null;
    mswServer.use(
      http.post('/api/v1/provider-connections', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(connection(), { status: 201 });
      }),
    );

    const onSaved = vi.fn();
    const { queryClient, result } = setup(chatgptModel, [], onSaved);
    expect(result.current.configured).toBe(false);

    act(() => result.current.setApiKey('sk-new-key'));
    act(() => result.current.saveMutation.mutate());
    await waitFor(() => expect(result.current.saveMutation.isSuccess).toBe(true));

    // Create payload: fixed direct transport + the engine's catalog route.
    expect(body).toEqual({
      transport_provider: 'openai',
      api_key: 'sk-new-key',
      routes: [{ logical_engine: 'chatgpt', transport_model: 'gpt-5.4', is_default: false }],
    });
    // Post-save: key + test state cleared, shared connections query invalidated.
    expect(result.current.apiKey).toBe('');
    expect(result.current.testResult).toBeNull();
    expect(queryClient.getQueryState(queryKeys.providers.connections())?.isInvalidated).toBe(true);
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it('rotates the key on the existing connection, preserving its routes', async () => {
    let body: Record<string, unknown> | null = null;
    mswServer.use(
      http.patch(`/api/v1/provider-connections/${CONNECTION_ID}`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(connection());
      }),
    );

    const { result } = setup(chatgptModel, [connection()]);
    expect(result.current.configured).toBe(true);

    act(() => result.current.setApiKey('sk-rotated'));
    act(() => result.current.saveMutation.mutate());
    await waitFor(() => expect(result.current.saveMutation.isSuccess).toBe(true));

    // Update payload: the merged route list keeps the existing chatgpt route
    // (no duplicate) and ships the new key.
    expect(body).toEqual({
      api_key: 'sk-rotated',
      routes: [{ logical_engine: 'chatgpt', transport_model: 'gpt-5.4', is_default: false }],
    });
  });

  it('omits api_key on update when no new key was entered (stored secret untouched)', async () => {
    let body: Record<string, unknown> | null = null;
    mswServer.use(
      http.patch(`/api/v1/provider-connections/${CONNECTION_ID}`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(connection());
      }),
    );

    const { result } = setup(chatgptModel, [connection()]);
    act(() => result.current.saveMutation.mutate());
    await waitFor(() => expect(result.current.saveMutation.isSuccess).toBe(true));

    expect(body).toEqual({
      routes: [{ logical_engine: 'chatgpt', transport_model: 'gpt-5.4', is_default: false }],
    });
  });

  it('surfaces a successful connection test with the transport model', async () => {
    mswServer.use(
      http.post(`/api/v1/provider-connections/${CONNECTION_ID}/test`, () =>
        HttpResponse.json(testResultPayload()),
      ),
    );

    const { result } = setup(chatgptModel, [connection()]);
    act(() => result.current.testMutation.mutate());
    await waitFor(() => expect(result.current.testMutation.isSuccess).toBe(true));

    expect(result.current.testResult).toEqual({
      status: 'ok',
      message: 'Connection succeeded (gpt-5.4).',
    });
  });

  it('surfaces a failed connection test with the server detail', async () => {
    mswServer.use(
      http.post(`/api/v1/provider-connections/${CONNECTION_ID}/test`, () =>
        HttpResponse.json(
          testResultPayload({
            status: 'failed',
            error_code: 'auth_failure',
            detail: 'Invalid API key',
          }),
        ),
      ),
    );

    const { result } = setup(chatgptModel, [connection()]);
    act(() => result.current.testMutation.mutate());
    await waitFor(() => expect(result.current.testMutation.isSuccess).toBe(true));

    expect(result.current.testResult).toEqual({ status: 'failed', message: 'Invalid API key' });
  });

  it('fails the test with a guard message when no connection exists yet', async () => {
    const { result } = setup(chatgptModel);
    act(() => result.current.testMutation.mutate());

    await waitFor(() =>
      expect(result.current.testResult).toEqual({
        status: 'failed',
        message: 'Save a key before testing.',
      }),
    );
  });

  it('fails the save when the catalog has no route for the engine', async () => {
    const noRoute: EngineCardModel = { logical_engine: 'chatgpt', label: 'ChatGPT', route: null };
    const { result } = setup(noRoute);

    act(() => result.current.setApiKey('sk-new-key'));
    act(() => result.current.saveMutation.mutate());
    await waitFor(() => expect(result.current.saveMutation.isError).toBe(true));

    expect(result.current.saveMutation.error).toBeInstanceOf(Error);
    expect((result.current.saveMutation.error as Error).message).toBe('No route available.');
  });
});
