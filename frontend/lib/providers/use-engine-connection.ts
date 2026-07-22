'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { providersApi } from '@/lib/api/providers';
import { queryKeys } from '@/lib/api/query-keys';
import type { ProviderConnection } from '@/lib/api/types';
import {
  connectionForTransport,
  isConfigured,
  mergeRoutePayload,
  type EngineCardModel,
} from './catalog';

/** Result of an inline "Test connection" run (the EngineCard alert model). */
export type ConnectionTestState = { status: 'ok' | 'failed'; message: string } | null;

/** Shared human-readable mutation error (matches the EngineCard fallback). */
export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'Something went wrong. Please try again.';
}

/**
 * Shared BYOK connection state machine for one logical engine (extracted from
 * `EngineCard` so the guided connect dialog can reuse it, Task 3.2).
 *
 * Owns the write-only API-key input state (never pre-filled — the stored
 * secret is never on the wire), the save mutation (create or rotate the
 * direct-transport connection and record the engine's catalog route), and the
 * "Test connection" mutation with the EngineCard success/failure alert model.
 * A successful save clears the key + test state and invalidates the shared
 * `providers.connections()` query; `onSaved` lets a host (e.g. the connect
 * dialog) react — typically by closing.
 */
export function useEngineConnection({
  model,
  connections,
  onSaved,
}: Readonly<{
  model: EngineCardModel;
  connections: ProviderConnection[];
  onSaved?: () => void;
}>) {
  const queryClient = useQueryClient();

  const route = model.route;
  const transport = route?.transport_provider ?? null;
  const [apiKey, setApiKey] = useState('');
  const [testResult, setTestResult] = useState<ConnectionTestState>(null);

  const connection = transport ? connectionForTransport(connections, transport) : undefined;
  const configured = isConfigured(connection);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!transport || !route) throw new Error('No route available.');
      const routes = mergeRoutePayload(connection, model.logical_engine, route.default_model);
      if (connection) {
        return providersApi.updateConnection(connection.id, {
          api_key: apiKey || undefined,
          routes,
        });
      }
      return providersApi.createConnection({
        transport_provider: transport,
        api_key: apiKey,
        routes,
      });
    },
    onSuccess: async () => {
      setApiKey('');
      setTestResult(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.providers.connections() });
      onSaved?.();
    },
  });

  const testMutation = useMutation({
    mutationFn: async () => {
      if (!connection) throw new Error('Save a key before testing.');
      return providersApi.testConnection(connection.id);
    },
    onSuccess: (result) => {
      setTestResult({
        status: result.status === 'ok' ? 'ok' : 'failed',
        message:
          result.status === 'ok'
            ? `Connection succeeded (${result.transport_model || 'model'}).`
            : result.detail || 'Connection failed.',
      });
    },
    onError: (error) => setTestResult({ status: 'failed', message: errorMessage(error) }),
  });

  const busy = saveMutation.isPending || testMutation.isPending;

  return {
    route,
    transport,
    connection,
    configured,
    apiKey,
    setApiKey,
    testResult,
    saveMutation,
    testMutation,
    busy,
  };
}
