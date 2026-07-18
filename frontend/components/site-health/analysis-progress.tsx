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
 * `pages` MUST be the monitored (selected) subset for this crawl, not an
 * unfiltered first cursor page: the monitored set is capped by the
 * entitlement's `monitored_url_limit` (currently <= 50) so a single bounded
 * page always contains every selected row, never a truncated slice that could
 * omit selected rows or admit `not_selected` ones into these crawl-wide counts.
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
  // Fallbacks while the crawl is still running: `score_summary` is only
  // written when the crawl terminalizes, so derive the live view from the
  // monitored pages subset instead of rendering 0s (`pages` is the complete
  // bounded monitored set — see the docstring above).
  const selected = summary?.selected_count || pages.length;
  const analyzed = summary?.analyzed_count ?? crawl.analyzed_count;
  const liveScores = summary ? null : computeLiveScores(pages);
  const overall = summary?.overall_score ?? liveScores?.overall ?? null;
  const technical = summary?.technical_score ?? liveScores?.technical ?? null;
  const aeo = summary?.aeo_score ?? liveScores?.aeo ?? null;

  // Per-status page counts drive the running / queued cards. `completed` uses
  // the server-aggregated `analyzed_count` (authoritative crawl-wide count)
  // rather than counting terminal statuses in `pages`, so it can never exceed
  // `selected` or drift from the scores above.
  const running = pages.filter((p) => p.analysis_status === 'running').length;
  const queued = pages.filter(
    (p) => p.analysis_status === 'pending' || p.analysis_status === 'not_selected',
  ).length;
  const completed = analyzed;

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
