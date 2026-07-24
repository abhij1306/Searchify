'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BarChart3, Globe, Info, Loader2, Search, type LucideIcon } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import type { RunStatusValue } from '@/components/ui/badge-variants';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow, CardHeader } from '@/components/ui/card';
import { Dialog } from '@/components/ui/dialog';
import { eyebrowClasses } from '@/components/ui/eyebrow';
import {
  integrationsApi,
  type IntegrationConnection,
  type IntegrationProvider,
  type IntegrationSyncRun,
} from '@/lib/api/integrations';
import { queryKeys } from '@/lib/api/query-keys';
import { formatShortDate, formatUtcTimestamp } from '@/lib/format';
import { isActiveSyncRun, SYNC_RUN_POLL_MS } from '@/lib/integrations/sync-runs';
import { assignLocation } from '@/lib/navigate';

/** OAuth grant family — gsc/ga4 share ONE Google grant; bing rides a Microsoft grant. */
export type GrantFamily = 'google' | 'microsoft';

/** Presentation model for one OAuth grant (connections grouped by `grant_id`). */
export type GrantModel = {
  grantId: string;
  family: GrantFamily;
  status: IntegrationConnection['grant_status'];
  scopes: string[];
  connections: IntegrationConnection[];
};

type GrantStatus = IntegrationConnection['grant_status'];
type SyncRunStatus = IntegrationSyncRun['status'];

export const GRANT_FAMILY: Record<IntegrationProvider, GrantFamily> = {
  gsc: 'google',
  ga4: 'google',
  bing: 'microsoft',
};

export const FAMILY_META: Record<
  GrantFamily,
  { title: string; connectProvider: IntegrationProvider; blurb: string }
> = {
  google: {
    title: 'Google',
    connectProvider: 'gsc',
    blurb: 'One consent links Search Console and Analytics 4 on a shared grant.',
  },
  microsoft: {
    title: 'Microsoft',
    connectProvider: 'bing',
    blurb: 'Links Bing Webmaster Tools through one Microsoft OAuth grant.',
  },
};

const PROVIDER_META: Record<IntegrationProvider, { label: string; Icon: LucideIcon }> = {
  gsc: { label: 'Google Search Console', Icon: Search },
  ga4: { label: 'Google Analytics 4', Icon: BarChart3 },
  bing: { label: 'Bing Webmaster Tools', Icon: Globe },
};

/** Grant lifecycle → status-badge token (connected→success, reauth/pending→warning, error→danger, revoked→neutral). */
const GRANT_STATUS_BADGE: Record<
  GrantStatus,
  { variant: 'status'; value: 'success' | 'warning' | 'danger' } | { variant: 'neutral' }
> = {
  connected: { variant: 'status', value: 'success' },
  needs_reauth: { variant: 'status', value: 'warning' },
  pending_revocation: { variant: 'status', value: 'warning' },
  error: { variant: 'status', value: 'danger' },
  revoked: { variant: 'neutral' },
};

const GRANT_STATUS_LABEL: Record<GrantStatus, string> = {
  connected: 'Connected',
  needs_reauth: 'Needs reauth',
  pending_revocation: 'Pending revocation',
  error: 'Error',
  revoked: 'Revoked',
};

/** Sync-run wire statuses rendered through the existing run-status badge family. */
const SYNC_RUN_BADGE: Record<SyncRunStatus, RunStatusValue> = {
  queued: 'queued',
  leased: 'queued',
  running: 'running',
  retry_wait: 'running',
  succeeded: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
};

/** Scope chips show the short scope name (`…/auth/webmasters.readonly` → `webmasters.readonly`). */
function scopeLabel(scope: string): string {
  const segment = scope.split('/').filter(Boolean).pop();
  return segment ?? scope;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return 'Something went wrong. Please try again.';
}

/** Grant-level alert for non-healthy lifecycle states (mockup alert idiom). */
function GrantAlert({ family, status }: Readonly<{ family: GrantFamily; status: GrantStatus }>) {
  const title = FAMILY_META[family].title;
  if (status === 'needs_reauth') {
    return (
      <Alert tone="warning">
        {title} requires renewed consent for this grant. Reconnect to resume syncing — previously
        imported data is unaffected.
      </Alert>
    );
  }
  if (status === 'error') {
    return (
      <Alert tone="danger">
        The last connection test failed — {title}&nbsp;rejected this grant&rsquo;s refresh.
        Reconnect to resume syncing.
      </Alert>
    );
  }
  if (status === 'pending_revocation') {
    return (
      <Alert tone="warning">
        Disconnect is finishing — Searchify is retrying the {title} revocation in the background.
        Previously imported data is kept.
      </Alert>
    );
  }
  if (status === 'revoked') {
    return (
      <Alert tone="neutral">This grant was revoked at {title}. Reconnect to resume syncing.</Alert>
    );
  }
  return null;
}

/**
 * One connection sub-row on a grant card: provider icon, label + account ref,
 * Test / Sync now / Disconnect actions, the active-run badge + note, and the
 * mono last-synced timestamp.
 *
 * Sync now enqueues via `POST /integrations/{id}/sync` (202), then polls
 * `GET …/syncs/{sync_id}` at `SYNC_RUN_POLL_MS` until the run reaches a
 * terminal status (`refetchInterval` returns `false`), which also invalidates
 * the connections list so the last-synced timestamp refreshes.
 */
function ConnectionRow({
  connection,
  grant,
}: Readonly<{ connection: IntegrationConnection; grant: GrantModel }>) {
  const queryClient = useQueryClient();
  const { label, Icon } = PROVIDER_META[connection.provider];
  const [testState, setTestState] = useState<{ ok: boolean; message: string } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [activeSyncId, setActiveSyncId] = useState<string | null>(null);

  const testMutation = useMutation({
    mutationFn: () => integrationsApi.test(connection.id),
    onSuccess: (result) => {
      setTestState(
        result.status === 'ok'
          ? { ok: true, message: 'Connection succeeded.' }
          : {
              ok: false,
              message: result.detail
                ? `Connection failed (${result.error_code || 'unknown'}): ${result.detail}`
                : `Connection failed (${result.error_code || 'unknown'}).`,
            },
      );
    },
    onError: (error) => setTestState({ ok: false, message: errorMessage(error) }),
  });

  const syncMutation = useMutation({
    mutationFn: () => integrationsApi.sync(connection.id),
    onSuccess: (enqueued) => {
      setTestState(null);
      setActiveSyncId(enqueued.sync_run_id);
    },
  });

  const syncRunQuery = useQuery({
    queryKey: queryKeys.integrations.sync(connection.id, activeSyncId ?? ''),
    queryFn: ({ signal }) => integrationsApi.getSync(connection.id, activeSyncId ?? '', { signal }),
    enabled: activeSyncId !== null,
    refetchInterval: (query) => {
      const run = query.state.data;
      if (!run) return SYNC_RUN_POLL_MS;
      return isActiveSyncRun(run.status) ? SYNC_RUN_POLL_MS : false;
    },
  });

  const activeRun = syncRunQuery.data ?? null;
  const runActive = activeRun !== null && isActiveSyncRun(activeRun.status);
  const runTerminal = activeRun !== null && !isActiveSyncRun(activeRun.status);

  // A finished run changes the connection's last_synced_at — refresh the list
  // (prefix-invalidation also covers the per-connection sync keys).
  useEffect(() => {
    if (runTerminal) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.integrations.all });
    }
  }, [runTerminal, queryClient]);

  const deleteMutation = useMutation({
    mutationFn: () => integrationsApi.delete(connection.id),
    onSuccess: async () => {
      setConfirmOpen(false);
      await queryClient.invalidateQueries({ queryKey: queryKeys.integrations.all });
    },
  });

  const busy = testMutation.isPending || syncMutation.isPending || deleteMutation.isPending;
  const lastConnection = grant.connections.length === 1;
  const siblings = grant.connections.filter((conn) => conn.id !== connection.id);
  const familyTitle = FAMILY_META[grant.family].title;

  return (
    <div
      className="border-border-subtle [&+&]:border-t"
      data-testid={`connection-row-${connection.provider}`}
    >
      <div className="flex items-center gap-3 px-3.5 py-3">
        <span
          aria-hidden
          className="bg-well text-secondary flex size-8 shrink-0 items-center justify-center rounded-md"
        >
          <Icon className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-foreground truncate text-sm font-semibold">{label}</div>
          <div className="text-muted truncate font-mono text-xs">{connection.account_ref}</div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => testMutation.mutate()}
            disabled={busy}
          >
            {testMutation.isPending ? 'Testing…' : 'Test'}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => syncMutation.mutate()}
            disabled={busy || runActive || grant.status !== 'connected'}
          >
            {syncMutation.isPending ? (
              <>
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                Syncing…
              </>
            ) : (
              'Sync now'
            )}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="text-danger-text hover:bg-danger-bg hover:text-danger-text"
            onClick={() => setConfirmOpen(true)}
            disabled={busy}
          >
            Disconnect
          </Button>
        </div>
      </div>

      {runActive && activeRun ? (
        <div className="flex items-center gap-2.5 px-3.5 pb-3 pl-[58px]">
          <Badge variant="run-status" value={SYNC_RUN_BADGE[activeRun.status]}>
            {activeRun.status.replace('_', ' ')}
          </Badge>
          <span className="text-muted text-2xs font-mono whitespace-nowrap">
            {activeRun.status === 'running'
              ? `${activeRun.row_count.toLocaleString('en-US')} rows · window ${formatShortDate(activeRun.window_start)}–${formatShortDate(activeRun.window_end)}`
              : `Enqueued ${formatUtcTimestamp(activeRun.created_at)} · waiting for a worker`}
          </span>
        </div>
      ) : null}

      <div className="flex items-center gap-2.5 px-3.5 pb-3 pl-[58px]">
        <span className={eyebrowClasses}>Last synced</span>
        <span className="text-secondary font-mono text-xs tabular-nums">
          {connection.last_synced_at ? formatUtcTimestamp(connection.last_synced_at) : 'Never'}
        </span>
      </div>

      {testState ? (
        <div className="px-3.5 pb-3 pl-[58px]">
          <Alert tone={testState.ok ? 'success' : 'danger'}>{testState.message}</Alert>
        </div>
      ) : null}
      {syncMutation.isError ? (
        <div className="px-3.5 pb-3 pl-[58px]">
          <Alert tone="danger">{errorMessage(syncMutation.error)}</Alert>
        </div>
      ) : null}

      <Dialog
        open={confirmOpen}
        onOpenChange={(open) => {
          if (!deleteMutation.isPending) setConfirmOpen(open);
        }}
        title={`Disconnect ${label}`}
        description={
          <>
            Remove <span className="font-mono text-xs">{connection.account_ref}</span> from this
            workspace?
          </>
        }
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending
                ? 'Disconnecting…'
                : lastConnection
                  ? 'Disconnect & revoke'
                  : 'Disconnect'}
            </Button>
          </>
        }
      >
        <div className="grid gap-2.5">
          {lastConnection ? (
            <>
              <p className="text-secondary text-sm">
                This is the{' '}
                <strong className="text-foreground font-semibold">last connection</strong> on the{' '}
                {familyTitle} OAuth grant, so disconnecting it also{' '}
                <strong className="text-foreground font-semibold">revokes the grant</strong>:
                Searchify&rsquo;s access at {familyTitle} is removed and the stored tokens are
                deleted. Previously imported {label} data is kept.
              </p>
              <p className="text-secondary text-sm">
                If {familyTitle}&nbsp;can&rsquo;t be reached to complete the revocation, the grant
                moves to{' '}
                <strong className="text-foreground font-semibold">pending revocation</strong> and
                Searchify retries in the background.
              </p>
            </>
          ) : (
            <>
              <p className="text-secondary text-sm">
                Searchify stops syncing {label} for{' '}
                <span className="font-mono text-xs">{connection.account_ref}</span> and removes this
                connection. Previously imported data is kept.
              </p>
              <p className="text-secondary text-sm">
                <strong className="text-foreground font-semibold">
                  {siblings.map((conn) => PROVIDER_META[conn.provider].label).join(' and ')} stays
                  connected
                </strong>
                , so the shared {familyTitle} OAuth grant remains active. The grant is only revoked
                — and {familyTitle} access removed for every connection — when its last connection
                is disconnected.
              </p>
            </>
          )}
          {deleteMutation.isError ? (
            <Alert tone="danger">{errorMessage(deleteMutation.error)}</Alert>
          ) : null}
        </div>
      </Dialog>
    </div>
  );
}

/**
 * Per-grant integration card (F5; mockups `integrations-settings-*.html`),
 * mirroring the providers `engine-card.tsx` idiom: an eyebrow + title header
 * with the grant-status badge, granted-scope chips, one sub-row per connection
 * on the grant, and a grant-level Reconnect footer.
 *
 * A `null` grant renders the family's not-connected card — the Connect action
 * is a full-page navigation to the same-origin OAuth start endpoint (a 302 —
 * never an apiClient fetch), which hands the browser to the provider consent
 * screen through the same-origin proxy (invariant 12).
 */
export function IntegrationCard({
  family,
  grant,
}: Readonly<{ family: GrantFamily; grant: GrantModel | null }>) {
  const meta = FAMILY_META[family];

  if (!grant) {
    return (
      <Card data-testid={`grant-card-${family}`}>
        <CardHeader className="flex-row items-start justify-between gap-2">
          <div className="grid gap-1">
            <CardEyebrow>OAuth grant</CardEyebrow>
            <h3 className="text-foreground text-base font-semibold">{meta.title}</h3>
            <p className="text-muted text-xs">{meta.blurb}</p>
          </div>
          <Badge variant="neutral">Not connected</Badge>
        </CardHeader>
        <CardContent>
          <div>
            <Button
              variant="secondary"
              onClick={() => assignLocation(integrationsApi.oauthStartUrl(meta.connectProvider))}
            >
              Connect {meta.title}
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const badge = GRANT_STATUS_BADGE[grant.status];
  return (
    <Card data-testid={`grant-card-${family}`}>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="grid min-w-0 gap-1">
          <CardEyebrow>OAuth grant</CardEyebrow>
          <h3 className="text-foreground text-base font-semibold">{meta.title}</h3>
          {grant.scopes.length > 0 ? (
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <span className={eyebrowClasses}>Scopes</span>
              {grant.scopes.map((scope) => (
                <Badge key={scope} variant="neutral" className="normal-case">
                  {scopeLabel(scope)}
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
        {badge.variant === 'status' ? (
          <Badge variant="status" value={badge.value} data-testid={`grant-status-${family}`}>
            {GRANT_STATUS_LABEL[grant.status]}
          </Badge>
        ) : (
          <Badge variant="neutral" data-testid={`grant-status-${family}`}>
            {GRANT_STATUS_LABEL[grant.status]}
          </Badge>
        )}
      </CardHeader>

      <CardContent className="grid gap-4">
        <GrantAlert family={family} status={grant.status} />

        <div className="text-muted flex items-center gap-1.5 text-xs">
          <Info className="size-3.5 shrink-0" aria-hidden />
          One OAuth grant shared by {grant.connections.length}{' '}
          {grant.connections.length === 1 ? 'connection' : 'connections'}.
        </div>

        <div className="border-border-subtle rounded-md border">
          {grant.connections.map((connection) => (
            <ConnectionRow key={connection.id} connection={connection} grant={grant} />
          ))}
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant={grant.status === 'connected' ? 'secondary' : 'primary'}
            onClick={() => assignLocation(integrationsApi.oauthStartUrl(meta.connectProvider))}
          >
            Reconnect
          </Button>
          <span className="flex-1" />
          <span className="text-muted text-right text-xs">
            Reconnecting renews consent for the whole grant.
          </span>
        </div>
      </CardContent>
    </Card>
  );
}
