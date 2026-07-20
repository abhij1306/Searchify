'use client';

import { Card, CardContent } from '@/components/ui/card';
import { ScoreRing } from '@/components/ui/score-ring';
import { Label } from '@/components/ui/typography';
import type { PageSummary, SiteCrawl, SiteHealthDashboard } from '@/lib/api/types';
import { PLACEHOLDER, formatScore } from '@/lib/site-health/status';

/**
 * Always-mounted score section of the canonical Site Health screen.
 *
 * The three score cards (Site Health / Technical / AEO) render in every phase:
 * placeholders before any analysis has produced data, a live running mean
 * while analysis is in flight, and the final `score_summary` once it lands.
 * Scores appear IN PLACE — the section never unmounts, so finishing a crawl
 * updates the cards instead of jumping to a different screen. Missing scores
 * render `—`, never a fabricated zero.
 */
export function ScoreSection({
  crawl,
  dashboard,
  pages,
  analyzing,
  selectedTotal,
}: Readonly<{
  crawl: SiteCrawl | null;
  dashboard: SiteHealthDashboard | undefined;
  /** Bounded monitored-page window — live score preview only, never counts. */
  pages: PageSummary[];
  /** True while analysis is running (enables the live running-mean fallback). */
  analyzing: boolean;
  /** This project's active monitored count; null until loaded. */
  selectedTotal: number | null;
}>) {
  const summary = dashboard?.score_summary ?? crawl?.score_summary ?? null;

  // Live scores kick in per-field whenever a terminal score is missing —
  // including a summary written with null metrics (e.g. mid-run projection).
  const liveScores =
    analyzing &&
    (summary === null ||
      summary.overall_score === null ||
      summary.technical_score === null ||
      summary.aeo_score === null)
      ? computeLiveScores(pages)
      : null;

  const overall = summary?.overall_score ?? liveScores?.overall ?? null;
  const technical = summary?.technical_score ?? liveScores?.technical ?? null;
  const aeo = summary?.aeo_score ?? liveScores?.aeo ?? null;

  return (
    <div className="grid gap-4 sm:grid-cols-3" data-testid="score-section">
      <ScoreCard
        label="Site Health"
        value={overall}
        sub={overallSub(summary, analyzing, crawl, selectedTotal)}
      />
      <ScoreCard label="Technical" value={technical} sub="Response codes, headers, delivery" />
      <ScoreCard label="AEO" value={aeo} sub="Schema, structured data, AI-readiness" />
    </div>
  );
}

function overallSub(
  summary: SiteHealthDashboard['score_summary'],
  analyzing: boolean,
  crawl: SiteCrawl | null,
  selectedTotal: number | null,
): string {
  if (summary) return `Across ${summary.analyzed_count} of ${summary.selected_count} pages`;
  if (analyzing && crawl) {
    return selectedTotal !== null
      ? `based on ${crawl.analyzed_count} of ${selectedTotal} pages`
      : `based on ${crawl.analyzed_count} pages so far`;
  }
  return 'Run the analysis to see scores';
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
    const values = scored.map(pick).filter((v): v is number => v !== null);
    if (values.length === 0) return null;
    return values.reduce((sum, v) => sum + v, 0) / values.length;
  };
  return {
    overall: mean((p) => p.overall_score),
    technical: mean((p) => p.technical_score),
    aeo: mean((p) => p.aeo_score),
  };
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
          <div className="border-border-subtle text-muted mono flex size-[72px] items-center justify-center rounded-full border text-lg">
            {PLACEHOLDER}
          </div>
        ) : (
          <ScoreRing value={value} size={72} label={`${label} score: ${Math.round(value)}`} />
        )}
        <div className="grid gap-0.5">
          <Label>{label}</Label>
          <span className="mono text-foreground text-lg font-semibold">
            {value === null ? PLACEHOLDER : `${formatScore(value)} / 100`}
          </span>
          <span className="text-muted text-xs">{sub}</span>
        </div>
      </CardContent>
    </Card>
  );
}
