'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';

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
 * shows `—`, not 0). The whole row is clickable and navigates to the Slice 8
 * per-URL detail route (`/site-health/crawls/[crawlId]/pages/[siteUrlId]`);
 * the View link remains as the keyboard/screen-reader affordance.
 */
function scoreClass(score: number | null): string {
  if (score === null) return 'text-muted';
  return scoreBandText[scoreBand(score)];
}

export function PagesTable({
  pages,
  crawlId,
}: Readonly<{ pages: PageSummary[]; crawlId: string }>) {
  const router = useRouter();
  const openPage = (siteUrlId: string) => {
    router.push(`/site-health/crawls/${crawlId}/pages/${siteUrlId}`);
  };
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead numeric className="w-10">#</TableHead>
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
          <TableRow
            key={page.site_url_id}
            onClick={() => openPage(page.site_url_id)}
            className="cursor-pointer"
          >
            <TableCell numeric className="text-muted">{index + 1}</TableCell>
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
            <TableCell className="whitespace-nowrap text-xs text-secondary">
              {formatAudited(page.last_audited)}
            </TableCell>
            <TableCell>
              <Link
                href={`/site-health/crawls/${crawlId}/pages/${page.site_url_id}`}
                onClick={(event) => event.stopPropagation()}
                className="text-xs font-medium text-accent-text hover:underline"
              >
                View
              </Link>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
