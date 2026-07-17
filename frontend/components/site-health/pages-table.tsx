'use client';

import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { scoreBand, scoreBandText } from '@/components/ui/score-band';
import { cn } from '@/lib/utils';
import type { PageSummary } from '@/lib/api/types';
import {
  formatAudited,
  formatIssueCount,
  formatScore,
  pageStatusBadgeValue,
  statusLabel,
} from '@/lib/site-health/status';

/**
 * Analyzed-pages table (Slice 7, mockups 712 + 713).
 *
 * Renders one row per analyzed page: URL (+ path), a per-page analysis status
 * badge (queued/running/completed/error/blocked), issue count, Technical / AEO
 * scores, last audited, and a View action. Missing / not-yet-analysed scores
 * render the `—` placeholder — never a fabricated zero (an error/blocked row
 * shows `—`, not 0). The View action is DISABLED until Slice 8 lands the
 * per-URL detail route (keeps the Slice 7 commit free of a broken route).
 */
function scoreClass(score: number | null): string {
  if (score === null) return 'text-muted';
  return scoreBandText[scoreBand(score)];
}

export function PagesTable({ pages }: Readonly<{ pages: PageSummary[] }>) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-10">#</TableHead>
          <TableHead>Page URL</TableHead>
          <TableHead>Status</TableHead>
          <TableHead numeric>Issues</TableHead>
          <TableHead numeric>Technical</TableHead>
          <TableHead numeric>AEO</TableHead>
          <TableHead>Last Audit</TableHead>
          <TableHead className="w-16" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {pages.map((page, index) => (
          <TableRow key={page.site_url_id}>
            <TableCell numeric className="text-muted">
              {index + 1}
            </TableCell>
            <TableCell>
              <span className="flex flex-col">
                <span className="font-medium text-foreground">
                  {page.title ?? page.display_url}
                </span>
                <span className="mono text-2xs text-muted">{page.display_url}</span>
              </span>
            </TableCell>
            <TableCell>
              <Badge variant="status" value={pageStatusBadgeValue(page.analysis_status)}>
                {statusLabel(page.analysis_status)}
              </Badge>
            </TableCell>
            <TableCell numeric className="mono text-danger-text">
              {formatIssueCount(page.issue_count)}
            </TableCell>
            <TableCell numeric className={cn('mono font-semibold', scoreClass(page.technical_score))}>
              {formatScore(page.technical_score)}
            </TableCell>
            <TableCell numeric className={cn('mono font-semibold', scoreClass(page.aeo_score))}>
              {formatScore(page.aeo_score)}
            </TableCell>
            <TableCell className="text-xs text-secondary">
              {formatAudited(page.last_audited)}
            </TableCell>
            <TableCell>
              {/* View is disabled until the Slice 8 per-URL detail route exists. */}
              <span
                aria-disabled="true"
                title="Available soon"
                className="cursor-not-allowed text-xs font-medium text-subtle"
              >
                View
              </span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
