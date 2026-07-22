import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { NO_RANKINGS_MESSAGE, RankingRowsTable } from '@/components/visibility/ranking-rows';
import type { Visibility } from '@/lib/api/types';
import { sortedRankings } from '@/lib/visibility/dashboard';

/**
 * Right "Rankings" card (design.md §9.6, shell-visibility-midnight mockup):
 * the dense brand-vs-competitor table (shared `RankingRowsTable`) under a
 * header pairing the title with the mono caption. Rows arrive SOV-sorted from
 * B6; `sortedRankings` keeps that order stable.
 */
export function RankingsTable({ visibility }: Readonly<{ visibility: Visibility }>) {
  const rows = sortedRankings(visibility.rankings);

  return (
    <Card>
      <CardHeader className="flex-row items-baseline justify-between gap-2 border-b-0">
        <CardTitle>Rankings</CardTitle>
        <span className="mono text-muted text-xs">brand vs competitors</span>
      </CardHeader>
      <CardContent className="p-0">
        {rows.length === 0 ? (
          <p className="text-secondary p-[var(--card-padding)] text-sm">{NO_RANKINGS_MESSAGE}</p>
        ) : (
          <RankingRowsTable rows={rows} />
        )}
      </CardContent>
    </Card>
  );
}
