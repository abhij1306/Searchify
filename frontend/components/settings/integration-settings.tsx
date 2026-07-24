'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  GRANT_FAMILY,
  IntegrationCard,
  type GrantFamily,
  type GrantModel,
} from '@/components/settings/integration-card';
import { IntegrationsEmptyState } from '@/components/settings/integrations-empty-state';
import { integrationsApi, type IntegrationConnection } from '@/lib/api/integrations';
import { queryKeys } from '@/lib/api/query-keys';
import { useProjectContext } from '@/lib/project/project-context';

/** Display order of connection sub-rows inside a grant card (gsc before ga4). */
const PROVIDER_ORDER: Record<IntegrationConnection['provider'], number> = {
  gsc: 0,
  ga4: 1,
  bing: 2,
};

const FAMILY_ORDER: GrantFamily[] = ['google', 'microsoft'];

/**
 * Group the flat `GET /integrations` connection list into per-grant cards:
 * connections sharing a `grant_id` ride ONE OAuth grant (one Google consent ⇒
 * gsc + ga4 rows; one Microsoft consent ⇒ bing). Grant status + scopes are
 * grant-level, so they are read off any connection in the group (deduped).
 */
function groupIntoGrants(connections: IntegrationConnection[]): GrantModel[] {
  const byGrant = new Map<string, IntegrationConnection[]>();
  for (const connection of connections) {
    const group = byGrant.get(connection.grant_id) ?? [];
    group.push(connection);
    byGrant.set(connection.grant_id, group);
  }
  const grants: GrantModel[] = [];
  for (const [grantId, grouped] of byGrant) {
    const sorted = [...grouped].sort(
      (a, b) => PROVIDER_ORDER[a.provider] - PROVIDER_ORDER[b.provider],
    );
    const first = sorted[0];
    grants.push({
      grantId,
      family: GRANT_FAMILY[first.provider],
      status: first.grant_status,
      scopes: [...new Set(sorted.flatMap((connection) => connection.granted_scopes))],
      connections: sorted,
    });
  }
  return grants.sort((a, b) => FAMILY_ORDER.indexOf(a.family) - FAMILY_ORDER.indexOf(b.family));
}

/**
 * OAuth-callback result notice (contract C2): the backend callback 302s to
 * `/settings?tab=integrations&connected=<provider>` / `&error=<code>`. The
 * params are captured once on mount and then stripped from the URL by the
 * panel, so a refresh never re-shows a stale notice.
 */
function CallbackNotice({
  notice,
}: Readonly<{ notice: { connected: string | null; error: string | null } }>) {
  if (notice.connected) {
    const family =
      notice.connected === 'gsc' || notice.connected === 'ga4' || notice.connected === 'bing'
        ? GRANT_FAMILY[notice.connected]
        : null;
    if (family === 'google') {
      return (
        <Alert tone="success">
          <strong className="font-semibold">Google connected.</strong> Search Console and Analytics
          4 are now linked on one shared OAuth grant. Initial syncs are queued and will appear in
          Traffic and LLM Analytics once they complete.
        </Alert>
      );
    }
    if (family === 'microsoft') {
      return (
        <Alert tone="success">
          <strong className="font-semibold">Microsoft connected.</strong> Bing Webmaster Tools is
          now linked. Initial syncs are queued and will appear in Traffic and LLM Analytics once
          they complete.
        </Alert>
      );
    }
    return (
      <Alert tone="success">
        <strong className="font-semibold">Integration connected.</strong> Initial syncs are queued
        and will appear in Traffic and LLM Analytics once they complete.
      </Alert>
    );
  }
  if (notice.error) {
    return (
      <Alert tone="danger">
        <strong className="font-semibold">Connection failed.</strong> The provider did not complete
        the connect flow (<code className="font-mono text-xs">{notice.error}</code>). No grant was
        created and nothing was stored — you can retry whenever you&rsquo;re ready.
      </Alert>
    );
  }
  return null;
}

/**
 * Settings → Integrations panel (F5): the 4th settings tab and the OAuth
 * callback landing surface (C2). Lists every workspace connection as per-grant
 * cards (Google → GSC + GA4 sub-rows on one shared grant; Microsoft → Bing),
 * shows the callback result notice, and offers Connect for any grant family
 * with no grant yet. Credentials never appear here — the backend never
 * serializes tokens (invariant 6) and Connect/Reconnect is a full-page 302
 * navigation, never an apiClient fetch.
 */
export function IntegrationSettings() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const { activeProject } = useProjectContext();
  const workspaceId = activeProject?.workspace_id ?? null;

  // C2 callback params — captured once (like the ?tab= deep link), then
  // cleaned from the URL so they don't linger across refresh/share.
  const [notice] = useState(() => ({
    connected: searchParams.get('connected'),
    error: searchParams.get('error'),
  }));
  const hasCallbackParams = searchParams.has('connected') || searchParams.has('error');
  useEffect(() => {
    if (!hasCallbackParams) return;
    // A fresh connect changes the list — refetch before stripping the params.
    if (notice.connected) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.integrations.all });
    }
    router.replace('/settings?tab=integrations');
  }, [hasCallbackParams, notice.connected, queryClient, router]);

  const connectionsQuery = useQuery({
    queryKey: queryKeys.integrations.connections(workspaceId),
    queryFn: ({ signal }) => integrationsApi.list({ signal }),
  });

  const connections = connectionsQuery.data ?? [];
  const grants = groupIntoGrants(connections);

  return (
    <div className="grid gap-5">
      <p className="text-secondary max-w-2xl text-sm">
        Connect first-party data to ground Traffic and LLM Analytics in real search performance.
        Search Console and Analytics 4 share one Google OAuth grant; Bing Webmaster Tools connects
        through Microsoft. Searchify requests read-only scopes and never displays credentials.
      </p>

      <CallbackNotice notice={notice} />

      {connectionsQuery.isError ? (
        <Alert tone="danger">
          Could not load integrations. Check your connection and try again.
        </Alert>
      ) : null}

      {connectionsQuery.isLoading ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {[0, 1].map((i) => (
            <Card key={i}>
              <CardContent className="grid gap-3">
                <Skeleton className="h-5 w-24" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-9 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      {!connectionsQuery.isLoading && !connectionsQuery.isError && connections.length === 0 ? (
        <IntegrationsEmptyState />
      ) : null}

      {!connectionsQuery.isLoading && !connectionsQuery.isError && connections.length > 0 ? (
        <div className="grid [grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr))] gap-4">
          {FAMILY_ORDER.map((family) => (
            <IntegrationCard
              key={family}
              family={family}
              grant={grants.find((grant) => grant.family === family) ?? null}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}
