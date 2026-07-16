import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card';
import { Donut, type DonutSegment } from '@/components/ui/donut';
import { ScoreRing } from '@/components/ui/score-ring';
import type { Visibility } from '@/lib/api/types';
import {
  PLACEHOLDER,
  engineLabel,
  formatRate,
  sortedRankings,
  visibleEngines,
  type VisibilityFilters,
} from '@/lib/visibility/dashboard';

// Bridged-token stroke palette for the Share-of-Voice donut (no raw hex): the
// brand takes the accent, competitors cycle through the score-band strokes.
const SOV_STROKES = [
  'stroke-score-good',
  'stroke-score-high',
  'stroke-score-mid',
  'stroke-citation-competitor',
] as const;

/**
 * Below-the-fold per-engine comparison + Share-of-Voice (design.md §9.6).
 *
 * A per-engine grid (one card per logical engine for the selected run, showing
 * that engine's visibility score, brand-mention rate, owned-citation rate and
 * search-use rate) plus a brand-vs-competitor Share-of-Voice donut. Honors the
 * engine filter; hidden entirely when the filtered set is empty.
 */
export function EngineComparison({
  visibility,
  filter,
}: Readonly<{ visibility: Visibility; filter: VisibilityFilters['engine'] }>) {
  const engines = visibleEngines(visibility, filter);

  return (
    <div className="grid gap-6 xl:grid-cols-[2fr_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>Per-engine comparison</CardTitle>
          <CardDescription>How each AI engine sees your brand in this run.</CardDescription>
        </CardHeader>
        <CardContent>
          {engines.length === 0 ? (
            <p className="text-sm text-secondary">No engine results match the current filter.</p>
          ) : (
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {engines.map((engine) => (
                <li
                  key={engine.logical_engine}
                  className="grid justify-items-center gap-3 rounded-md border border-border-subtle bg-background-alt p-4 text-center"
                >
                  <p className="text-sm font-semibold text-foreground">
                    {engineLabel(engine.logical_engine)}
                  </p>
                  <ScoreRing
                    value={engine.visibility_score ?? 0}
                    size={80}
                    strokeWidth={8}
                    showValue={engine.visibility_score !== null}
                    label={`${engineLabel(engine.logical_engine)} visibility score: ${
                      engine.visibility_score === null ? 'not available' : Math.round(engine.visibility_score)
                    }%`}
                  />
                  <dl className="grid w-full gap-1 text-xs">
                    <EngineStat label="Brand mentions" value={formatRate(engine.brand_mention_rate)} />
                    <EngineStat label="Owned citations" value={formatRate(engine.owned_citation_rate)} />
                    <EngineStat label="Search used" value={formatRate(engine.search_use_rate)} />
                    <EngineStat label="Responses" value={`${engine.total_completed}`} />
                  </dl>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <ShareOfVoiceCard visibility={visibility} />
    </div>
  );
}

function EngineStat({ label, value }: Readonly<{ label: string; value: string }>) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-secondary">{label}</dt>
      <dd className="mono font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function ShareOfVoiceCard({ visibility }: Readonly<{ visibility: Visibility }>) {
  const rows = sortedRankings(visibility.rankings).filter((row) => (row.mention_count ?? 0) > 0);
  const segments: DonutSegment[] = rows.map((row, index) => ({
    label: row.name,
    value: row.mention_count,
    colorClass: row.is_brand ? 'stroke-accent' : SOV_STROKES[index % SOV_STROKES.length],
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Share of Voice</CardTitle>
        <CardDescription>Mention share, brand vs. competitors.</CardDescription>
      </CardHeader>
      <CardContent>
        {segments.length === 0 ? (
          <p className="text-sm text-secondary">{PLACEHOLDER} No mentions recorded for this run.</p>
        ) : (
          <Donut segments={segments} label="Share of voice" size={132} strokeWidth={16} />
        )}
      </CardContent>
    </Card>
  );
}
