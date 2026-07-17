'use client';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label, Metric } from '@/components/ui/typography';
import { PagesTable } from '@/components/site-health/pages-table';
import type { PageSummary, SiteCrawl } from '@/lib/api/types';
import { crawlBadgeValue, formatScore, statusLabel } from '@/lib/site-health/status';

/**
 * Live analysis-progress view (Slice 7, mockup 712).
 *
 * While the selected pages are analyzed, shows the running overall/Technical/AEO
 * scores (based on `analyzed / selected` pages), a total/completed/in-progress/
 * queued breakdown, and the per-page audit status table where rows move
 * queued → running → completed/error/blocked (driven by the parent's polling +
 * SSE invalidation, no reload). A Cancel control is offered while active.
 * Scores render `—` when not yet produced (never a fabricated zero).
 */
export function AnalysisProgress({
  crawl,
  pages,
  onCancel,
  cancelPending,
}: Readonly<{
  crawl: SiteCrawl;
  pages: PageSummary[];
  onCancel: () => void;
  cancelPending: boolean;
}>) {
  const summary = crawl.score_summary;
  const selected = summary?.selected_count ?? 0;
  const analyzed = summary?.analyzed_count ?? crawl.analyzed_count;

  // Per-status page counts drive the queued / running / completed cards.
  const running = pages.filter((p) => p.analysis_status === 'running').length;
  const queued = pages.filter(
    (p) => p.analysis_status === 'pending' || p.analysis_status === 'not_selected',
  ).length;
  const completed = pages.filter((p) =>
    ['completed', 'partially_completed', 'failed', 'error', 'blocked', 'cancelled'].includes(
      p.analysis_status,
    ),
  ).length;

  return (
    <div className="grid gap-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <ScoreCell label="Site Health" value={summary?.overall_score ?? null} sub={`based on ${analyzed} of ${selected} pages`} />
        <ScoreCell label="Technical" value={summary?.technical_score ?? null} />
        <ScoreCell label="AEO" value={summary?.aeo_score ?? null} />
      </div>

      <Card>
        <CardContent className="grid gap-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Badge variant="run-status" value={crawlBadgeValue(crawl.status)}>
                {statusLabel(crawl.status)}
              </Badge>
              <span className="text-sm text-secondary" aria-live="polite">
                Auditing selected pages for technical and AEO health issues
              </span>
            </div>
            <Button variant="destructive" size="sm" onClick={onCancel} disabled={cancelPending}>
              {cancelPending ? 'Cancelling…' : 'Cancel'}
            </Button>
          </div>

          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <CountCell label="Total pages" value={selected} />
            <CountCell label="Completed" value={completed} className="text-run-completed" />
            <CountCell label="In progress" value={running} className="text-run-running" />
            <CountCell label="Queued" value={queued} className="text-muted" />
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-0">
          <PagesTable pages={pages} crawlId={crawl.id} />
        </CardContent>
      </Card>
    </div>
  );
}

function ScoreCell({
  label,
  value,
  sub,
}: Readonly<{ label: string; value: number | null; sub?: string }>) {
  return (
    <Card>
      <CardContent className="grid gap-1">
        <Label>{label}</Label>
        <Metric className="text-2xl">{formatScore(value)}</Metric>
        {sub ? <span className="text-xs text-muted">{sub}</span> : null}
      </CardContent>
    </Card>
  );
}

function CountCell({
  label,
  value,
  className,
}: Readonly<{ label: string; value: number; className?: string }>) {
  return (
    <div className="grid gap-1">
      <Label>{label}</Label>
      <Metric className={`text-xl ${className ?? ''}`}>{value}</Metric>
    </div>
  );
}
