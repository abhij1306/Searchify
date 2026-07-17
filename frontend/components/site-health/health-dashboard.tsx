'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ScoreRing } from '@/components/ui/score-ring';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/typography';
import { PagesTable } from '@/components/site-health/pages-table';
import { siteHealthQueries, type PagesParams } from '@/lib/api/site-health';
import type { SiteCrawl, SiteHealthDashboard } from '@/lib/api/types';
import { cn } from '@/lib/utils';
import { PLACEHOLDER, formatScore } from '@/lib/site-health/status';

/** The three server-backed page tabs (design mockup 713). */
type TabKey = 'monitored' | 'all' | 'errors';

const TABS: ReadonlyArray<{ key: TabKey; label: string; params: PagesParams }> = [
  { key: 'monitored', label: 'Monitored', params: { monitored: true } },
  { key: 'all', label: 'All Discovered', params: {} },
  { key: 'errors', label: 'Errors & Blocked', params: { status: 'error_or_blocked' } },
];

const PAGE_LIMIT = 25;

/**
 * Completed Site Health dashboard (Slice 7, mockup 713).
 *
 * Renders the crawl's overall/Technical/AEO score rings + a coverage summary,
 * then a server-backed, cursor-safe page browser with three tabs: monitored,
 * all discovered, and errors/blocked. Each tab is its OWN query with its own
 * cursor — filtering is server-side, never a filter over the current client
 * page. Missing scores render `—`, never a fabricated zero. Exports and re-crawl
 * are wired by the parent. View actions stay disabled until Slice 8.
 */
export function HealthDashboard({
  dashboard,
  crawl,
  active,
}: Readonly<{
  dashboard: SiteHealthDashboard;
  crawl: SiteCrawl;
  active: boolean;
}>) {
  const summary = dashboard.score_summary ?? crawl.score_summary;
  const [tab, setTab] = useState<TabKey>('monitored');
  // Per-tab cursor stack so Prev/Next walk keyset pages without offsets.
  const [cursorStack, setCursorStack] = useState<Record<TabKey, string[]>>({
    monitored: [],
    all: [],
    errors: [],
  });

  const activeTab = TABS.find((t) => t.key === tab)!;
  const cursor = cursorStack[tab].at(-1) ?? undefined;

  const pagesQuery = useQuery({
    ...siteHealthQueries.pages(crawl.id, { ...activeTab.params, cursor, limit: PAGE_LIMIT }),
    // While analysis is active the dashboard keeps refreshing (polling baseline).
    refetchInterval: active ? 4_000 : false,
  });

  const rows = pagesQuery.data?.items ?? [];
  const nextCursor = pagesQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack[tab].length > 0;

  const selectTab = (next: TabKey) => setTab(next);
  const goNext = () => {
    if (!nextCursor) return;
    setCursorStack((prev) => ({ ...prev, [tab]: [...prev[tab], nextCursor] }));
  };
  const goPrev = () =>
    setCursorStack((prev) => ({ ...prev, [tab]: prev[tab].slice(0, -1) }));

  return (
    <div className="grid gap-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <ScoreCard label="Site Health" value={summary?.overall_score ?? null} sub={coverageSub(summary)} />
        <ScoreCard label="Technical" value={summary?.technical_score ?? null} sub="Response codes, headers, delivery" />
        <ScoreCard label="AEO" value={summary?.aeo_score ?? null} sub="Schema, structured data, AI-readiness" />
      </div>

      <Card>
        <CardContent className="grid gap-4 p-0">
          <div className="flex flex-wrap items-center gap-1 border-b border-border-subtle px-[var(--card-padding)] pt-[var(--card-padding)]">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => selectTab(t.key)}
                aria-current={t.key === tab ? 'true' : undefined}
                className={cn(
                  'rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors',
                  t.key === tab
                    ? 'border-accent text-foreground'
                    : 'border-transparent text-secondary hover:text-foreground',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          {pagesQuery.isError ? (
            <div className="p-[var(--card-padding)]">
              <Alert tone="danger">Could not load pages for this view. Try again.</Alert>
            </div>
          ) : pagesQuery.isLoading ? (
            <div className="grid gap-2 p-[var(--card-padding)]">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : rows.length === 0 ? (
            <p className="p-[var(--card-padding)] text-sm text-secondary">
              No pages in this view.
            </p>
          ) : (
            <PagesTable pages={rows} crawlId={crawl.id} />
          )}

          <div className="flex items-center justify-end gap-2 border-t border-border-subtle px-[var(--card-padding)] py-3">
            <Button variant="secondary" size="sm" onClick={goPrev} disabled={!canPrev}>
              Previous
            </Button>
            <Button variant="secondary" size="sm" onClick={goNext} disabled={!nextCursor}>
              Next
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function coverageSub(summary: SiteHealthDashboard['score_summary']): string {
  if (!summary) return 'No pages analyzed yet';
  return `Across ${summary.analyzed_count} of ${summary.selected_count} pages`;
}

function ScoreCard({
  label,
  value,
  sub,
}: Readonly<{ label: string; value: number | null; sub: string }>) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4">
        {value === null ? (
          <div className="flex size-[72px] items-center justify-center rounded-full border border-border-subtle text-lg text-muted mono">
            {PLACEHOLDER}
          </div>
        ) : (
          <ScoreRing value={value} size={72} label={`${label} score: ${Math.round(value)}`} />
        )}
        <div className="grid gap-0.5">
          <Label>{label}</Label>
          <span className="mono text-lg font-semibold text-foreground">
            {value === null ? PLACEHOLDER : `${formatScore(value)} / 100`}
          </span>
          <span className="text-xs text-muted">{sub}</span>
        </div>
      </CardContent>
    </Card>
  );
}
