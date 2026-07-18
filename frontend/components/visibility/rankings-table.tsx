import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
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
import type { Visibility } from '@/lib/api/types';
import { PLACEHOLDER, formatRate, sortedRankings } from '@/lib/visibility/dashboard';

/**
 * Right "Rankings" card (design.md §9.6): a dense brand-vs-competitor table.
 * Columns are `#`, Brand (name + a "You" pill on the own brand), Visibility%
 * (mono + score-band color), SOV% (mono), Sentiment and Avg Position — the last
 * two render the "—" not-yet-computed placeholder (decision B-2). Rows arrive
 * SOV-sorted from B6; `sortedRankings` keeps that order stable.
 */
export function RankingsTable({ visibility }: Readonly<{ visibility: Visibility }>) {
  const rows = sortedRankings(visibility.rankings);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Rankings</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {rows.length === 0 ? (
          <p className="p-[var(--card-padding)] text-sm text-secondary">
            No brand or competitor mentions were recorded for this run.
          </p>
        ) : (
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
                const visibilityPct = row.mention_rate === null ? null : Math.round(row.mention_rate * 100);
                const bandClass =
                  visibilityPct === null ? 'text-muted' : scoreBandText[scoreBand(visibilityPct)];
                return (
                  <TableRow key={`${row.is_brand ? 'brand' : 'competitor'}-${row.name}`}>
                    <TableCell numeric className="text-muted">
                      {index + 1}
                    </TableCell>
                    <TableCell>
                      <span className="flex items-center gap-2">
                        <span className="font-medium text-foreground">{row.name}</span>
                        {row.is_brand ? (
                          <Badge variant="neutral" className="normal-case">
                            You
                          </Badge>
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
        )}
      </CardContent>
    </Card>
  );
}
