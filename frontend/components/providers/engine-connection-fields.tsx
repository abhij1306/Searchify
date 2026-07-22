'use client';

import { Alert } from '@/components/ui/alert';
import { Field } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { errorMessage, type useEngineConnection } from '@/lib/providers/use-engine-connection';

/**
 * Shared BYOK key field + save/test feedback for one engine connection — the
 * presentation half of `useEngineConnection`, rendered identically by the
 * Settings `EngineCard` and the guided `ConnectProviderDialog`. The key input
 * stays write-only (never pre-filled — the stored secret is never on the
 * wire); this component is the single home for that invariant's copy.
 */
export function EngineConnectionFields({
  state,
}: Readonly<{
  state: ReturnType<typeof useEngineConnection>;
}>) {
  const { configured, apiKey, setApiKey, saveMutation, testResult } = state;

  return (
    <>
      <Field
        label={configured ? 'API key (enter a new key to rotate)' : 'API key'}
        hint="Write-only — your stored key is never displayed."
      >
        {(props) => (
          <Input
            {...props}
            type="password"
            autoComplete="off"
            placeholder={configured ? '•••••••• stored' : 'Paste your API key'}
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
          />
        )}
      </Field>

      {saveMutation.isError ? (
        <Alert tone="danger">{errorMessage(saveMutation.error)}</Alert>
      ) : null}

      {testResult ? (
        <Alert tone={testResult.status === 'ok' ? 'success' : 'danger'}>{testResult.message}</Alert>
      ) : null}
    </>
  );
}
