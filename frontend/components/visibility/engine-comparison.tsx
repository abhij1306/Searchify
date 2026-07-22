import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardEyebrow,
} from '@/components/ui/card';
import { ScoreRing } from '@/components/ui/score-ring';
import { cn } from '@/lib/utils';
import type { RankingRow, Visibility } from '@/lib/api/types';
import {
  PLACEHOLDER,
  engineLabel,
  formatRate,
  sortedRankings,
  visibleEngines,
  type VisibilityFilters,
} from '@/lib/visibility/dashboard';

// Engine identity dots cycle through the accent (blue) family — decorative
// only, so bridged-token opacity steps stand in for the mockup's blue-1/2/3
// ramp (no raw hex, both themes stay in-family).
const ENGINE_DOTS = ['bg-accent/60', 'bg-accent', 'bg-accent-hover'] as const;

/**
 * Below-the-fold per-engine comparison + Share-of-Voice (design.md §9.6,
 * shell-visibility-midnight mockup).
 *
 * A per-engine grid (one tile per logical engine for the selected run: engine
 * dot + name, the visibility score ring with mono numeral, and the mono stat
 * rows) plus the brand-vs-competitor Share-of-Voice bars — the brand row
 * takes the accent-gradient fill (`.accent-gradient-bar`), competitors a
 * muted fill on the well track. Honors the engine filter; hidden entirely
 * when the filtered set is empty.
 */
export function EngineComparison({
  visibility,
  filter,
}: Readonly<{ visibility: Visibility; filter: VisibilityFilters['engine'] }>) {
  const engines = visibleEngines(visibility, filter);

  return (
    <div className="grid gap-5 xl:grid-cols-[2fr_1fr]">
      <Card>
        <CardHeader>
          <CardTitle>Per-engine comparison</CardTitle>
          <CardDescription>How each AI engine sees your brand in this run.</CardDescription>
        </CardHeader>
        <CardContent>
          {engines.length === 0 ? (
            <p className="text-secondary text-sm">No engine results match the current filter.</p>
          ) : (
            <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {engines.map((engine, index) => (
                <li
                  key={engine.logical_engine}
                  className="border-border-subtle bg-background-alt grid justify-items-center gap-3 rounded-lg border p-4 text-center"
                >
                  <p className="text-foreground flex items-center gap-2 text-sm font-semibold">
                    <span
                      aria-hidden
                      className={cn(
                        'size-1.5 shrink-0 rounded-full',
                        ENGINE_DOTS[index % ENGINE_DOTS.length],
                      )}
                    />
                    {engineLabel(engine.logical_engine)}
                  </p>
                  <ScoreRing
                    value={engine.visibility_score ?? 0}
                    size={80}
                    strokeWidth={8}
                    showValue={engine.visibility_score !== null}
                    label={`${engineLabel(engine.logical_engine)} visibility score: ${
                      engine.visibility_score === null
                        ? 'not available'
                        : Math.round(engine.visibility_score)
                    }%`}
                  />
                  <dl className="grid w-full gap-1 text-xs">
                    <EngineStat
                      label="Brand mentions"
                      value={formatRate(engine.brand_mention_rate)}
                    />
                    <EngineStat
                      label="Owned citations"
                      value={formatRate(engine.owned_citation_rate)}
                    />
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
      <dd className="mono text-foreground font-semibold">{value}</dd>
    </div>
  );
}

function ShareOfVoiceCard({ visibility }: Readonly<{ visibility: Visibility }>) {
  const rows = sortedRankings(visibility.rankings).filter((row) => (row.mention_count ?? 0) > 0);
  const total = rows.reduce((sum, row) => sum + (row.mention_count ?? 0), 0);
  // Same ARIA summary contract the donut figure exposed.
  const summary =
    total > 0
      ? rows
          .map((row) => `${row.name} ${Math.round(((row.mention_count ?? 0) / total) * 100)}%`)
          .join(', ')
      : 'No data';

  return (
    <Card>
      <CardHeader className="flex-row items-baseline justify-between gap-2">
        <CardEyebrow>Share of voice</CardEyebrow>
        <span className="mono text-muted text-xs">mentions across engines</span>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-secondary text-sm">{PLACEHOLDER} No mentions recorded for this run.</p>
        ) : (
          <div role="img" aria-label={`Share of voice: ${summary}`} className="grid gap-3.5">
            {rows.map((row) => (
              <ShareOfVoiceRow
                key={`${row.is_brand ? 'brand' : 'competitor'}-${row.name}`}
                row={row}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ShareOfVoiceRow({ row }: Readonly<{ row: RankingRow }>) {
  const pct =
    row.share_of_voice === null
      ? 0
      : Math.max(0, Math.min(100, Math.round(row.share_of_voice * 100)));
  return (
    <div className="grid grid-cols-[92px_1fr_44px] items-center gap-3">
      <span
        className={cn(
          'truncate text-sm',
          row.is_brand ? 'text-foreground font-semibold' : 'text-secondary font-medium',
        )}
      >
        {row.name}
      </span>
      <span className="bg-well h-2 overflow-hidden rounded-full">
        <span
          className={cn(
            'block h-full rounded-full',
            row.is_brand ? 'accent-gradient-bar' : 'bg-foreground/20',
          )}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span
        className={cn(
          'mono text-right text-xs',
          row.is_brand ? 'text-foreground' : 'text-secondary',
        )}
      >
        {formatRate(row.share_of_voice)}
      </span>
    </div>
  );
}
