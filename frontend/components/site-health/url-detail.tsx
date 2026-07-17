'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ScoreRing } from '@/components/ui/score-ring';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/typography';
import { queryKeys } from '@/lib/api/query-keys';
import { siteHealthMutations, siteHealthQueries } from '@/lib/api/site-health';
import type { DeliveryFacts, PageDetail, SiteIssue } from '@/lib/api/types';
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

/**
 * Per-URL Site Health detail (Slice 8, mockup 711).
 *
 * Renders URL metadata, overall/Technical/AEO score rings, persisted delivery
 * facts (HTTP-level, not field CWV), the page's current issues ordered by
 * severity, and paginated crawl-bounded issue history. A "Re-audit this page"
 * action re-queues analysis (persisted server-side). Missing scores render `—`,
 * never a fabricated zero.
 */
export function UrlDetail({
  crawlId,
  siteUrlId,
}: Readonly<{ crawlId: string; siteUrlId: string }>) {
  const detailQuery = useQuery(siteHealthQueries.page(crawlId, siteUrlId));
  const detail = detailQuery.data ?? null;

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

      <HeaderCard detail={detail} crawlId={crawlId} siteUrlId={siteUrlId} />

      <div className="grid gap-4 sm:grid-cols-3">
        <ScoreTile label="Technical Health" value={detail.technical_score} />
        <ScoreTile label="AEO Health" value={detail.aeo_score} />
        <ScoreTile label="Combined" value={detail.overall_score} />
      </div>

      <DeliveryMetrics delivery={detail.delivery} />

      <IssuesList issues={detail.issues} />

      <IssueHistory crawlId={crawlId} siteUrlId={siteUrlId} />
    </div>
  );
}

function HeaderCard({
  detail,
  crawlId,
  siteUrlId,
}: Readonly<{ detail: PageDetail; crawlId: string; siteUrlId: string }>) {
  const queryClient = useQueryClient();
  const [reaudited, setReaudited] = useState(false);
  const rerun = useMutation({
    ...siteHealthMutations.rerunPage(),
    onSuccess: async () => {
      setReaudited(true);
      await queryClient.invalidateQueries({
        queryKey: queryKeys.siteHealth.page(crawlId, siteUrlId),
      });
    },
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
            onClick={() => rerun.mutate({ crawlId, siteUrlId })}
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
    { label: 'Cache-Control', value: delivery.cache_control ?? 'no-cache' },
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
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setCursorStack((prev) => prev.slice(0, -1))}
              disabled={!canPrev}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                nextCursor && setCursorStack((prev) => [...prev, nextCursor])
              }
              disabled={!nextCursor}
            >
              Next
            </Button>
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
