'use client';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow, CardHeader } from '@/components/ui/card';
import { eyebrowClasses } from '@/components/ui/eyebrow';
import type { ProviderConnection } from '@/lib/api/types';
import { TRANSPORT_LABELS, type EngineCardModel } from '@/lib/providers/catalog';
import { useEngineConnection } from '@/lib/providers/use-engine-connection';

import { EngineConnectionFields } from './engine-connection-fields';

/**
 * Per-engine provider card (F8, v2 direct-provider retirement).
 *
 * Renders one logical engine served by a single fixed direct transport
 * (ChatGPT/OpenAI, Gemini/Google, Claude/Anthropic): a write-only API-key
 * input (never pre-filled — the stored secret is never on the wire), a "Test
 * connection" action, and a `configured` status badge driven by the
 * connection's `api_key_set` flag. The save/test state machine lives in the
 * shared `useEngineConnection` hook (Task 3.2) so the guided connect dialog
 * behaves identically.
 */
export function EngineCard({
  model,
  connections,
}: Readonly<{ model: EngineCardModel; connections: ProviderConnection[] }>) {
  const connectionState = useEngineConnection({ model, connections });
  const { route, transport, connection, configured, apiKey, saveMutation, testMutation, busy } =
    connectionState;

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <div className="grid gap-1">
          <CardEyebrow>AI engine</CardEyebrow>
          <h3 className="text-foreground text-base font-semibold">{model.label}</h3>
          <div className="flex items-center gap-2">
            {transport ? (
              <Badge variant="neutral">via {TRANSPORT_LABELS[transport]}</Badge>
            ) : (
              <span className="text-muted text-xs">No route available</span>
            )}
          </div>
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
        {route ? (
          <div className="grid gap-1.5">
            <span className={eyebrowClasses}>Route</span>
            <span className="text-foreground text-sm">{route.label}</span>
            {route.default_model ? (
              <span className="text-2xs text-muted font-mono">Model: {route.default_model}</span>
            ) : null}
          </div>
        ) : null}

        <EngineConnectionFields state={connectionState} />

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
