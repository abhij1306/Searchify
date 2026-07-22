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
import type { RankingRow } from '@/lib/api/types';
import { PLACEHOLDER, formatRate } from '@/lib/visibility/dashboard';

/** Shared empty state for a rankings table with no rows. */
export const NO_RANKINGS_MESSAGE = 'No brand or competitor mentions were recorded for this run.';

/**
 * Shared brand-vs-competitor rankings table (design.md §9.6), used by both the
 * selected-run Rankings card and the trend-mode ranking-history cards. Columns
 * are `#`, Brand (name + a "You" pill on the own brand), Visibility% (mono +
 * score-band color), SOV% (mono), Sentiment and Avg Position — the last two
 * render the "—" not-yet-computed placeholder (decision B-2).
 */
export function RankingRowsTable({ rows }: Readonly<{ rows: readonly RankingRow[] }>) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-10">#</TableHead>
          <TableHead>Brand</TableHead>
          <TableHead numeric>Visibility</TableHead>
          <TableHead numeric>SOV</TableHead>
          <TableHead numeric>Sentiment</TableHead>
          <TableHead numeric>Avg Position</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, index) => {
          const visibilityPct =
            row.mention_rate === null ? null : Math.round(row.mention_rate * 100);
          const bandClass =
            visibilityPct === null ? 'text-muted' : scoreBandText[scoreBand(visibilityPct)];
          return (
            <TableRow key={`${row.is_brand ? 'brand' : 'competitor'}-${row.name}`}>
              <TableCell numeric className="text-muted">
                {index + 1}
              </TableCell>
              <TableCell>
                <span className="flex items-center gap-2">
                  <span className="text-foreground font-medium">{row.name}</span>
                  {row.is_brand ? (
                    // Midnight "YOU" pill chip (mockup .you-pill): mono,
                    // uppercase, raised-well fill — distinct from the Badge
                    // family (no status dot).
                    <span className="bg-well text-foreground text-2xs inline-flex items-center rounded-full px-2 py-0.5 font-mono leading-[1.4] font-semibold tracking-[0.08em] uppercase">
                      You
                    </span>
                  ) : null}
                </span>
              </TableCell>
              <TableCell numeric className={cn('mono font-semibold', bandClass)}>
                {formatRate(row.mention_rate)}
              </TableCell>
              <TableCell numeric className="mono text-foreground">
                {formatRate(row.share_of_voice)}
              </TableCell>
              <TableCell numeric className="mono text-muted">
                {PLACEHOLDER}
              </TableCell>
              <TableCell numeric className="mono text-muted">
                {PLACEHOLDER}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
