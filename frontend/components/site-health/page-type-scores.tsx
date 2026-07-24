'use client';

import { Card, CardContent } from '@/components/ui/card';
import { scoreTextClass } from '@/components/ui/score-band';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Label } from '@/components/ui/typography';
import { PageTypeBadge } from '@/components/site-health/page-type-badge';
import type { SiteCrawl, SiteHealthDashboard } from '@/lib/api/types';
import { byPageTypeRows } from '@/lib/site-health/page-types';
import { formatScore } from '@/lib/site-health/status';
import { cn } from '@/lib/utils';

/**
 * Dashboard per-page-type score breakdown (site-health v2 P1).
 *
 * Renders `score_summary.by_page_type` — one row per classified page type
 * with its analyzed count and mean Technical/AEO/overall scores. Like the
 * score cards, the panel is data-driven: it appears once a score summary
 * exists (a mid-run projection included) and follows the same
 * dashboard-then-crawl fallback. An empty breakdown means analysis has not
 * classified any page yet; missing mean scores render `—`, never a
 * fabricated zero.
 */
export function PageTypeScores({
  crawl,
  dashboard,
}: Readonly<{
  crawl: SiteCrawl | null;
  dashboard: SiteHealthDashboard | undefined;
}>) {
  const summary = dashboard?.score_summary ?? crawl?.score_summary ?? null;
  if (summary === null) return null;

  const rows = byPageTypeRows(summary.by_page_type);

  return (
    <Card data-testid="page-type-scores">
      <CardContent className="grid gap-3">
        <div className="grid gap-0.5">
          <Label className="font-mono tracking-[0.08em]">Scores by Page Type</Label>
          <span className="text-secondary text-sm">
            Mean scores across the analyzed pages of each type.
          </span>
        </div>
        {rows.length === 0 ? (
          <p className="text-secondary text-sm">
            Per-page-type scores appear once the analysis classifies your pages.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Page Type</TableHead>
                <TableHead numeric>Analyzed</TableHead>
                <TableHead numeric>Technical</TableHead>
                <TableHead numeric>AEO</TableHead>
                <TableHead numeric>Overall</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.page_type}>
                  <TableCell>
                    <PageTypeBadge pageType={row.page_type} />
                  </TableCell>
                  <TableCell numeric className="mono text-secondary">
                    {row.analyzed_count}
                  </TableCell>
                  <TableCell
                    numeric
                    className={cn('mono font-semibold', scoreTextClass(row.technical_score))}
                  >
                    {formatScore(row.technical_score)}
                  </TableCell>
                  <TableCell
                    numeric
                    className={cn('mono font-semibold', scoreTextClass(row.aeo_score))}
                  >
                    {formatScore(row.aeo_score)}
                  </TableCell>
                  <TableCell
                    numeric
                    className={cn('mono font-semibold', scoreTextClass(row.overall_score))}
                  >
                    {formatScore(row.overall_score)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
