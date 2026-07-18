'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CursorPager } from '@/components/ui/cursor-pager';
import { Card, CardContent } from '@/components/ui/card';
import { ScoreRing } from '@/components/ui/score-ring';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/typography';
import { ApiError } from '@/lib/api/errors';
import { queryKeys } from '@/lib/api/query-keys';
import { siteHealthMutations, siteHealthQueries } from '@/lib/api/site-health';
import type { DeliveryFacts, PageDetail, RerunPageResponse, SiteIssue } from '@/lib/api/types';
import {
  dimensionLabel,
  issueTitle,
  severityBadgeValue,
  severityLabel,
  severityRank,
} from '@/lib/site-health/issues';
import {
  PLACEHOLDER,
  formatAudited,
  pageStatusBadgeValue,
  statusLabel,
} from '@/lib/site-health/status';
import { cn } from '@/lib/utils';

const HISTORY_LIMIT = 25;
const RERUN_POLL_INTERVAL_MS = 3_000;

/**
 * Per-URL Site Health detail (Slice 8, mockup 711).
 *
 * Renders URL metadata, overall/Technical/AEO score rings, persisted delivery
 * facts (HTTP-level, not field CWV), the page's current issues ordered by
 * severity, and paginated crawl-bounded issue history. A "Re-audit this page"
 * action re-queues analysis (persisted server-side). Missing scores render `—`,
 * never a fabricated zero.
 */
/**
 * Search param the rerun flow appends when it navigates to the canonical
 * detail route of a *freshly minted* rerun crawl (`created_new_crawl`). On the
 * fresh mount it seeds `rerunPolling = true` so polling begins immediately
 * against the new crawl identity rather than waiting for a manual reload.
 */
const RERUN_SEARCH_PARAM = 'rerun';

export function UrlDetail({
  crawlId,
  siteUrlId,
}: Readonly<{ crawlId: string; siteUrlId: string }>) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  // A rerun that minted a NEW crawl navigates here with `?rerun=1`; start
  // polling on mount so the fresh run's queued/running progress is observed
  // without a manual reload.
  const [rerunPolling, setRerunPolling] = useState(
    () => searchParams.get(RERUN_SEARCH_PARAM) === '1',
  );
  // Guards against the immediately-post-mutation snapshot: right after the
  // rerun mutation resolves, the page-detail query cache still holds the
  // *previous* run's terminal `analysis_status` (e.g. `'completed'`) until a
  // refetch lands. Without this, the terminal-status effect below would see
  // that stale terminal snapshot and turn polling off before ever observing
  // the freshly-enqueued task's `'pending'`/`'running'` state. We only allow
  // polling to stop once we've actually observed a non-terminal snapshot
  // since the rerun was requested. A ref (not state): it is never rendered,
  // so updating it must not trigger a re-render.
  const hasObservedActiveRerunRef = useRef(false);
  const [rerunError, setRerunError] = useState<string | null>(null);
  const detailQuery = useQuery({
    ...siteHealthQueries.page(crawlId, siteUrlId),
    // Poll while a rerun is in flight so the snapshot advances past the
    // queued/running state without a manual reload; stop once the analysis
    // reaches a terminal presentation status.
    refetchInterval: (query) => {
      if (!rerunPolling) return false;
      const status = query.state.data?.analysis_status;
      if (status === 'pending' || status === 'running' || status === undefined) {
        return RERUN_POLL_INTERVAL_MS;
      }
      return false;
    },
  });
  const detail = detailQuery.data ?? null;

  useEffect(() => {
    if (!rerunPolling || !detail) return;
    if (detail.analysis_status === 'pending' || detail.analysis_status === 'running') {
      hasObservedActiveRerunRef.current = true;
      return;
    }
    // Terminal status observed. Only stop polling once we've actually seen
    // the rerun take effect (a pending/running snapshot); otherwise this is
    // just the stale pre-rerun cache and we must keep polling for it to
    // update.
    if (hasObservedActiveRerunRef.current) {
      setRerunPolling(false);
    }
  }, [rerunPolling, detail]);

  if (detailQuery.isLoading) {
    return (
      <div className="grid gap-6">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (detailQuery.isError || !detail) {
    return <Alert tone="danger">Could not load this page. It may not exist in this crawl.</Alert>;
  }

  return (
    <div className="grid gap-6">
      <nav className="text-xs text-muted" aria-label="Breadcrumb">
        <Link href="/site-health" className="hover:text-accent">
          Site Health
        </Link>
        <span className="px-1.5" aria-hidden>
          /
        </span>
        <span className="text-secondary">{detail.title ?? detail.display_url}</span>
      </nav>

      <HeaderCard
        key={`header:${crawlId}:${siteUrlId}`}
        detail={detail}
        crawlId={crawlId}
        siteUrlId={siteUrlId}
        onRerunStart={() => setRerunError(null)}
        onRerunError={(error) => {
          if (error instanceof ApiError && error.status === 409) {
            setRerunError(
              'This page is not part of the active monitored selection, so it cannot be re-audited. Add it to your monitored set first.',
            );
          } else if (error instanceof ApiError && error.status === 403) {
            setRerunError('Re-auditing pages requires a Starter plan.');
          } else {
            setRerunError('Could not re-audit this page. Please try again.');
          }
        }}
        onRerunComplete={(result) => {
          // The backend may run the rerun in the SAME active crawl or mint a
          // fresh single-page crawl (when the source crawl was terminal). Poll
          // the identity the response points at — never the terminal source.
          if (result.crawl_id === crawlId && result.site_url_id === siteUrlId) {
            // Same crawl: poll in place. Reset the guard so the terminal-check
            // effect can't stop polling on the stale pre-rerun snapshot.
            hasObservedActiveRerunRef.current = false;
            setRerunPolling(true);
            return;
          }
          // Fresh crawl: seed the new page's cache with the returned
          // non-terminal baseline so polling starts from a known state, then
          // navigate to the canonical detail route for the new identity with
          // `?rerun=1` so the remounted component begins polling immediately.
          queryClient.setQueryData<PageDetail>(
            queryKeys.siteHealth.page(result.crawl_id, result.site_url_id),
            (prev) =>
              prev
                ? { ...prev, crawl_id: result.crawl_id, analysis_status: result.analysis_status }
                : prev,
          );
          router.push(
            `/site-health/crawls/${result.crawl_id}/pages/${result.site_url_id}?${RERUN_SEARCH_PARAM}=1`,
          );
        }}
      />

      {rerunError ? <Alert tone="danger">{rerunError}</Alert> : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <ScoreTile label="Technical Health" value={detail.technical_score} />
        <ScoreTile label="AEO Health" value={detail.aeo_score} />
        <ScoreTile label="Combined" value={detail.overall_score} />
      </div>

      <DeliveryMetrics delivery={detail.delivery} />

      <IssuesList issues={detail.issues} />

      <IssueHistory key={`history:${crawlId}:${siteUrlId}`} crawlId={crawlId} siteUrlId={siteUrlId} />
    </div>
  );
}

function HeaderCard({
  detail,
  crawlId,
  siteUrlId,
  onRerunStart,
  onRerunError,
  onRerunComplete,
}: Readonly<{
  detail: PageDetail;
  crawlId: string;
  siteUrlId: string;
  onRerunStart: () => void;
  onRerunError: (error: unknown) => void;
  onRerunComplete: (result: RerunPageResponse) => void;
}>) {
  const queryClient = useQueryClient();
  const [reaudited, setReaudited] = useState(false);
  const rerun = useMutation({
    ...siteHealthMutations.rerunPage(),
    onSuccess: async (result) => {
      setReaudited(true);
      // When the rerun stays in the SAME crawl, invalidate its page query so
      // polling refetches the freshly-enqueued pending/running snapshot. When
      // it minted a NEW crawl, the parent navigates to the new identity, so
      // invalidating the source crawl's (now-terminal) query is unnecessary.
      if (result.crawl_id === crawlId && result.site_url_id === siteUrlId) {
        await queryClient.invalidateQueries({
          queryKey: queryKeys.siteHealth.page(crawlId, siteUrlId),
        });
      }
      onRerunComplete(result);
    },
    onError: onRerunError,
  });

  return (
    <Card>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <h1 className="text-xl font-semibold text-foreground">
              {detail.title ?? detail.display_url}
            </h1>
          </div>
          <Button
            size="sm"
            onClick={() => {
              onRerunStart();
              rerun.mutate({ crawlId, siteUrlId });
            }}
            disabled={rerun.isPending}
          >
            {rerun.isPending ? 'Re-auditing…' : reaudited ? 'Re-audit queued' : 'Re-audit this page'}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-secondary">
          <span className="flex items-center gap-1.5">
            <Label>URL</Label>
            <a
              href={detail.display_url}
              target="_blank"
              rel="noopener noreferrer"
              className="mono text-accent-text hover:underline"
            >
              {detail.display_url}
            </a>
          </span>
          <span className="flex items-center gap-1.5">
            <Label>Last Audit</Label>
            <span>{formatAudited(detail.last_audited)}</span>
          </span>
          <span className="flex items-center gap-1.5">
            <Label>Status</Label>
            <Badge variant="status" value={pageStatusBadgeValue(detail.analysis_status)}>
              {statusLabel(detail.analysis_status)}
            </Badge>
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function ScoreTile({ label, value }: Readonly<{ label: string; value: number | null }>) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-2 py-6">
        {value === null ? (
          <div className="mono flex size-[64px] items-center justify-center rounded-full border border-border-subtle text-lg text-muted">
            {PLACEHOLDER}
          </div>
        ) : (
          <ScoreRing value={value} size={64} label={`${label}: ${Math.round(value)}`} />
        )}
        <Label>{label}</Label>
      </CardContent>
    </Card>
  );
}

/** Persisted HTTP delivery facts (mockup 711 "Delivery Metrics"). */
function DeliveryMetrics({ delivery }: Readonly<{ delivery: DeliveryFacts }>) {
  const items: Array<{ label: string; value: string }> = [
    { label: 'TTFB', value: delivery.ttfb_ms === null ? PLACEHOLDER : `${Math.round(delivery.ttfb_ms)}ms` },
    { label: 'Response Size', value: formatBytes(delivery.decoded_bytes ?? delivery.html_bytes) },
    { label: 'HTTP Status', value: delivery.status_code === null ? PLACEHOLDER : `${delivery.status_code}` },
    { label: 'Compression', value: delivery.compression ?? 'none' },
    { label: 'HTTP Version', value: delivery.http_version ?? PLACEHOLDER },
    { label: 'Cache-Control', value: delivery.cache_control ?? PLACEHOLDER },
    {
      label: 'Blocking Resources',
      value:
        delivery.blocking_resource_count === null
          ? PLACEHOLDER
          : `${delivery.blocking_resource_count}`,
    },
    { label: 'Wire Size', value: formatBytes(delivery.wire_bytes) },
  ];
  return (
    <Card>
      <CardContent className="grid gap-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground">Delivery Metrics</h2>
          <span className="text-2xs text-muted">
            Static HTTP-level measurements (not browser-rendered Core Web Vitals)
          </span>
        </div>
        <dl className="grid gap-4 sm:grid-cols-4">
          {items.map((item) => (
            <div key={item.label} className="grid gap-0.5">
              <Label>{item.label}</Label>
              <dd className="mono text-sm font-semibold text-foreground">{item.value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

/** Current issues on the page, ordered by severity (mockup 711 "All Issues"). */
function IssuesList({ issues }: Readonly<{ issues: SiteIssue[] }>) {
  const ordered = [...issues].sort(
    (a, b) => severityRank(a.severity) - severityRank(b.severity),
  );
  return (
    <Card>
      <CardContent className="grid gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground">
            All Issues ({issues.length})
          </h2>
          <span className="text-2xs text-muted">Sorted by severity</span>
        </div>
        {ordered.length === 0 ? (
          <p className="text-sm text-secondary">No issues detected on this page.</p>
        ) : (
          <ol className="divide-y divide-border-subtle">
            {ordered.map((issue, index) => (
              <li
                key={issue.id}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <span className="flex min-w-0 items-center gap-3">
                  <span className="mono w-6 shrink-0 text-xs text-muted">{index + 1}</span>
                  <span className="truncate text-sm text-foreground">{issueTitle(issue)}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <Badge
                    className={cn(issue.dimension === 'aeo' ? 'text-accent' : 'text-info-text')}
                  >
                    {dimensionLabel(issue.dimension)}
                  </Badge>
                  <Badge variant="status" value={severityBadgeValue(issue.severity)}>
                    {severityLabel(issue.severity)}
                  </Badge>
                </span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

/** Paginated, crawl-bounded per-URL issue history (newest first). */
function IssueHistory({
  crawlId,
  siteUrlId,
}: Readonly<{ crawlId: string; siteUrlId: string }>) {
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const cursor = cursorStack.at(-1);
  const historyQuery = useQuery(
    siteHealthQueries.issueHistory(crawlId, siteUrlId, { cursor, limit: HISTORY_LIMIT }),
  );
  const rows = historyQuery.data?.items ?? [];
  const nextCursor = historyQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack.length > 0;

  return (
    <Card>
      <CardContent className="grid gap-3">
        <h2 className="text-base font-semibold text-foreground">Issue History</h2>
        {historyQuery.isError ? (
          <Alert tone="danger">Could not load issue history.</Alert>
        ) : historyQuery.isLoading ? (
          <div className="grid gap-2">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <p className="text-sm text-secondary">No prior issue records for this page.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {rows.map((row) => (
              <li key={row.id} className="flex items-center justify-between gap-3 py-2.5">
                <span className="flex min-w-0 flex-col">
                  <span className="truncate text-sm text-foreground">{issueTitle(row)}</span>
                  <span className="text-2xs text-muted">{formatAudited(row.created_at)}</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <Badge
                    className={cn(row.dimension === 'aeo' ? 'text-accent' : 'text-info-text')}
                  >
                    {dimensionLabel(row.dimension)}
                  </Badge>
                  <Badge variant="status" value={severityBadgeValue(row.severity)}>
                    {severityLabel(row.severity)}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        )}
        {rows.length > 0 ? (
          <div className="flex items-center justify-end gap-2">
            <CursorPager
              canPrev={canPrev}
              canNext={Boolean(nextCursor)}
              onPrev={() => setCursorStack((prev) => prev.slice(0, -1))}
              onNext={() =>
                nextCursor &&
                setCursorStack((prev) =>
                  // Idempotent under rapid clicks: the captured nextCursor may
                  // already be on the stack before the rerender lands.
                  prev.at(-1) === nextCursor ? prev : [...prev, nextCursor],
                )
              }
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Human-readable byte size (KB) or the placeholder. */
function formatBytes(bytes: number | null): string {
  if (bytes === null) return PLACEHOLDER;
  if (bytes < 1024) return `${bytes} B`;
  return `${Math.round((bytes / 1024) * 10) / 10} KB`;
}
