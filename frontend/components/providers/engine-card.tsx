'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { providersApi } from '@/lib/api/providers';
import { queryKeys } from '@/lib/api/query-keys';
import type { ProviderConnection, TransportProvider } from '@/lib/api/types';
import {
  connectionForTransport,
  isConfigured,
  mergeRoutePayload,
  TRANSPORT_LABELS,
  type EngineCardModel,
} from '@/lib/providers/catalog';
import { RouteToggle } from './route-toggle';

type TestState = { status: 'ok' | 'failed'; message: string } | null;

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'Something went wrong. Please try again.';
}

/**
 * Per-engine provider card (F8, design.md §9.5).
 *
 * Owns the local UI state for one logical engine: the selected transport route
 * (segmented toggle for Gemini/Claude; fixed OpenRouter for ChatGPT), a
 * write-only API-key input (never pre-filled — the stored secret is never on
 * the wire), a "Test connection" action, and a `configured` status badge driven
 * by the connection's `api_key_set` flag. Saving creates or rotates the BYOK
 * connection for the selected transport and records the engine's route.
 */
export function EngineCard({
  model,
  connections,
}: Readonly<{ model: EngineCardModel; connections: ProviderConnection[] }>) {
  const queryClient = useQueryClient();

  const enabledOptions = model.options.filter((o) => !o.disabled);
  const defaultTransport = enabledOptions[0]?.transport_provider ?? null;

  const [transport, setTransport] = useState<TransportProvider | null>(defaultTransport);
  const [apiKey, setApiKey] = useState('');
  const [testResult, setTestResult] = useState<TestState>(null);

  const selectedOption = model.options.find((o) => o.transport_provider === transport);
  const connection = transport ? connectionForTransport(connections, transport) : undefined;
  const configured = isConfigured(connection);

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.providers.connections() });

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!transport || !selectedOption) throw new Error('Select a route first.');
      const routes = mergeRoutePayload(connection, model.logical_engine, selectedOption.default_model);
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
      await invalidate();
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
  const keyFieldId = useMemo(() => `key-${model.logical_engine}`, [model.logical_engine]);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <div className="grid gap-0.5">
          <h3 className="text-base font-semibold text-foreground">{model.label}</h3>
          <p className="text-xs text-secondary">
            {transport ? `via ${TRANSPORT_LABELS[transport]}` : 'No route available'}
          </p>
        </div>
        {configured ? (
          <Badge variant="status" value="success">
            Configured
          </Badge>
        ) : (
          <Badge variant="neutral">Not configured</Badge>
        )}
      </CardHeader>

      <CardContent className="grid gap-4">
        {model.options.length > 0 ? (
          <div className="grid gap-1.5">
            <span className="text-xs font-medium text-secondary">Route</span>
            {/* ChatGPT: OpenRouter-only + a disabled "coming soon" option.
                Gemini/Claude: a real direct/OpenRouter toggle. */}
            <RouteToggle
              options={model.options}
              value={transport}
              onChange={(next) => {
                setTransport(next);
                setTestResult(null);
              }}
              idBase={`route-${model.logical_engine}`}
            />
            {selectedOption?.default_model ? (
              <span className="text-2xs text-muted">Model: {selectedOption.default_model}</span>
            ) : null}
          </div>
        ) : null}

        <Field
          label={configured ? 'API key (enter a new key to rotate)' : 'API key'}
          hint="Write-only — your stored key is never displayed."
        >
          {(props) => (
            <Input
              {...props}
              id={keyFieldId}
              type="password"
              autoComplete="off"
              placeholder={configured ? '•••••••• stored' : 'Paste your API key'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          )}
        </Field>

        {saveMutation.isError ? (
          <Alert tone="danger">{errorMessage(saveMutation.error)}</Alert>
        ) : null}

        {testResult ? (
          <Alert tone={testResult.status === 'ok' ? 'success' : 'danger'}>{testResult.message}</Alert>
        ) : null}

        <div className="flex items-center gap-2">
          <Button
            type="button"
            onClick={() => saveMutation.mutate()}
            disabled={busy || !transport || (!apiKey && !configured)}
          >
            {saveMutation.isPending ? 'Saving…' : configured ? 'Update key' : 'Save key'}
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => testMutation.mutate()}
            disabled={busy || !connection}
          >
            {testMutation.isPending ? 'Testing…' : 'Test connection'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
