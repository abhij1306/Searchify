'use client';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog } from '@/components/ui/dialog';
import { Field } from '@/components/ui/field';
import { inputClasses } from '@/components/ui/input';
import { providersApi } from '@/lib/api/providers';
import { queryKeys } from '@/lib/api/query-keys';
import type { LogicalEngine, ProviderConnection } from '@/lib/api/types';
import {
  buildEngineCards,
  connectionForTransport,
  ENGINE_LABELS,
  ENGINE_ORDER,
  isConfigured,
  TRANSPORT_LABELS,
  type EngineCardModel,
} from '@/lib/providers/catalog';
import { useEngineConnection } from '@/lib/providers/use-engine-connection';

import { EngineConnectionFields } from './engine-connection-fields';

/**
 * ConnectProviderDialog (Task 3.2) — the guided single-provider connect flow,
 * reusable from the Getting Started checklist and the launch dialog's "No
 * configured engines" empty state. One engine picker (driven by the provider
 * catalog), one write-only API-key field (never pre-filled — the stored
 * secret is never on the wire), an inline "Test connection" with the same
 * success/failure UX as the Settings engine cards, then Save. A successful
 * save invalidates the shared connections query (so callers like the launch
 * dialog see the new engine immediately) and closes. Full 3-engine management
 * stays in Settings → Providers.
 */
export function ConnectProviderDialog({
  open,
  onOpenChange,
  onConnected,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a key is saved successfully (the dialog closes first). */
  onConnected?: () => void;
}>) {
  const catalogQuery = useQuery({
    queryKey: queryKeys.providers.catalog(),
    queryFn: ({ signal }) => providersApi.getCatalog({ signal }),
    enabled: open,
  });
  const connectionsQuery = useQuery({
    queryKey: queryKeys.providers.connections(),
    queryFn: ({ signal }) => providersApi.listConnections({ signal }),
    enabled: open,
  });

  const cards = buildEngineCards(catalogQuery.data);
  const connections = connectionsQuery.data ?? [];

  // Reset the picker each time the dialog opens so it always lands on the
  // first engine that still needs a key (adjust-state-during-render pattern).
  const [selected, setSelected] = useState<LogicalEngine | null>(null);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setSelected(null);
  }

  const firstUnconfigured =
    cards.find((card) =>
      card.route
        ? !isConfigured(connectionForTransport(connections, card.route.transport_provider))
        : true,
    )?.logical_engine ?? ENGINE_ORDER[0];
  const engine = selected ?? firstUnconfigured;
  const model = cards.find((card) => card.logical_engine === engine) ?? cards[0];

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Connect a provider"
      description="Pick an AI engine and paste its API key. Keys are write-only — Searchify never displays a stored secret."
    >
      <div className="grid gap-4">
        <Field label="AI engine">
          {(props) => (
            <select
              {...props}
              className={inputClasses}
              value={engine}
              onChange={(event) => setSelected(event.target.value as LogicalEngine)}
            >
              {ENGINE_ORDER.map((key) => (
                <option key={key} value={key}>
                  {ENGINE_LABELS[key]}
                </option>
              ))}
            </select>
          )}
        </Field>

        <ConnectEngineForm
          key={engine}
          model={model}
          connections={connections}
          onCancel={() => onOpenChange(false)}
          onSaved={() => {
            onOpenChange(false);
            onConnected?.();
          }}
        />
      </div>
    </Dialog>
  );
}

/**
 * Per-engine connect form. Keyed by the picked engine so switching engines
 * resets the typed key and test state. Save/test semantics mirror the
 * Settings engine card exactly via the shared `useEngineConnection` hook.
 */
function ConnectEngineForm({
  model,
  connections,
  onCancel,
  onSaved,
}: Readonly<{
  model: EngineCardModel;
  connections: ProviderConnection[];
  onCancel: () => void;
  onSaved: () => void;
}>) {
  const connectionState = useEngineConnection({ model, connections, onSaved });
  const { route, transport, connection, configured, apiKey, saveMutation, testMutation, busy } =
    connectionState;

  return (
    <div className="grid gap-4">
      {route ? (
        <p className="text-muted text-sm">
          via {TRANSPORT_LABELS[route.transport_provider]}
          {route.default_model ? (
            <>
              {' · '}
              <span className="text-2xs font-mono">{route.default_model}</span>
            </>
          ) : null}
        </p>
      ) : (
        <p className="text-muted text-sm">No route available for this engine.</p>
      )}

      <EngineConnectionFields state={connectionState} />

      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          variant="secondary"
          onClick={() => testMutation.mutate()}
          disabled={busy || !connection}
        >
          {testMutation.isPending ? 'Testing…' : 'Test connection'}
        </Button>
        <Button
          onClick={() => saveMutation.mutate()}
          disabled={busy || !transport || (!apiKey && !configured)}
        >
          {saveMutation.isPending ? 'Saving…' : configured ? 'Update key' : 'Save key'}
        </Button>
      </div>
    </div>
  );
}
