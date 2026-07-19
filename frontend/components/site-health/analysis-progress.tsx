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
 *
 * `pages` is a bounded window of the monitored subset used for the per-page
 * table and the live score preview only. The COUNTS never depend on it: with
 * env-raised limits the monitored set can be thousands of URLs, far beyond one
 * page fetch, so total/completed/queued derive from server-side counters —
 * `selectedTotal` (this project's active monitored count) and the crawl's
 * aggregated `analyzed_count` / `failed_count`.
 */
export function AnalysisProgress({
  crawl,
  pages,
  selectedTotal,
  onCancel,
  cancelPending,
}: Readonly<{
  crawl: SiteCrawl;
  pages: PageSummary[];
  /** This project's active monitored (selected) URL count; null until loaded. */
  selectedTotal: number | null;
  onCancel: () => void;
  cancelPending: boolean;
}>) {
  const summary = crawl.score_summary;
  // Fallbacks while the crawl is still running: `score_summary` is only
  // written when the crawl terminalizes, so derive the live view from server
  // counters instead of rendering 0s. `pages.length` is the last resort (a
  // bounded window, but better than nothing before the quota loads).
  const selected = summary?.selected_count ?? selectedTotal ?? pages.length;
  const analyzed = summary?.analyzed_count ?? crawl.analyzed_count;
  // Live scores kick in per-field whenever a terminal score is missing —
  // including a summary written with null metrics (e.g. mid-run projection).
  const liveScores =
    summary?.overall_score != null &&
    summary.technical_score != null &&
    summary.aeo_score != null
      ? null
      : computeLiveScores(pages);
  const overall = summary?.overall_score ?? liveScores?.overall ?? null;
  const technical = summary?.technical_score ?? liveScores?.technical ?? null;
  const aeo = summary?.aeo_score ?? liveScores?.aeo ?? null;

  // `completed`/`failed` use the server-aggregated crawl counters
  // (authoritative crawl-wide counts). `running` is observed from the visible
  // window (no server counter exists for it), and `queued` is the arithmetic
  // remainder — clamped at 0 so a transiently-stale mix of counters can never
  // render a negative count.
  const running = pages.filter((p) => p.analysis_status === 'running').length;
  const completed = analyzed;
  const failed = crawl.failed_count;
  const queued = Math.max(0, selected - completed - failed - running);

  return (
    <div className="grid gap-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <ScoreCell label="Site Health" value={overall} sub={`based on ${analyzed} of ${selected} pages`} />
        <ScoreCell label="Technical" value={technical} />
        <ScoreCell label="AEO" value={aeo} />
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

/**
 * Running mean of the per-page scores that have landed so far. Only pages with
 * a completed analysis contribute; returns null (rendered as `—`) until at
 * least one page has scores — never a fabricated zero.
 */
function computeLiveScores(
  pages: PageSummary[],
): { overall: number | null; technical: number | null; aeo: number | null } | null {
  const scored = pages.filter((p) => p.overall_score !== null);
  if (scored.length === 0) return null;
  const mean = (pick: (p: PageSummary) => number | null) => {
    const values = scored
      .map(pick)
      .filter((v): v is number => v !== null);
    if (values.length === 0) return null;
    return values.reduce((sum, v) => sum + v, 0) / values.length;
  };
  return {
    overall: mean((p) => p.overall_score),
    technical: mean((p) => p.technical_score),
    aeo: mean((p) => p.aeo_score),
  };
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
