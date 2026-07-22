import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardEyebrow } from '@/components/ui/card';
import { scoreBand, scoreBandText } from '@/components/ui/score-band';
import { ScoreRing } from '@/components/ui/score-ring';
import { cn } from '@/lib/utils';
import type { RunStatusValue } from '@/components/ui/badge-variants';
import type { AuditStatus, Visibility } from '@/lib/api/types';

const RUN_STATUS_LABELS: Partial<Record<AuditStatus, string>> = {
  completed: 'Completed',
  partially_completed: 'Partially completed',
};

const RUN_STATUS_BADGE: Partial<Record<AuditStatus, RunStatusValue>> = {
  completed: 'completed',
  partially_completed: 'partial',
};

/**
 * Left "Visibility" card (design.md §9.6, shell-visibility-midnight mockup):
 * the mono panel-label eyebrow, the run's Visibility Score in a `score-ring`
 * with a large mono numeral (score-band colored), its subtitle, the run
 * status pill chip, and the completed/failed mono stats.
 */
export function VisibilityScoreCard({ visibility }: Readonly<{ visibility: Visibility }>) {
  const label = RUN_STATUS_LABELS[visibility.audit_status] ?? visibility.audit_status;
  const badge = RUN_STATUS_BADGE[visibility.audit_status] ?? 'completed';
  // Same clamp ScoreRing applies, so the overlaid display numeral always
  // matches the ring's arc and its ARIA label.
  const clamped = Math.max(0, Math.min(100, Math.round(visibility.visibility_score)));

  return (
    <Card>
      <CardContent className="grid justify-items-center gap-4 text-center">
        <CardEyebrow>Visibility score</CardEyebrow>
        <div className="relative inline-flex">
          <ScoreRing
            value={visibility.visibility_score}
            size={128}
            strokeWidth={10}
            showValue={false}
          />
          {/* Midnight display numeral — larger than ScoreRing's built-in label
              (the mockup's 31px mono); aria-hidden, the ring's svg carries the
              accessible "Visibility score: N%" label. */}
          <span
            aria-hidden
            className={cn(
              'mono absolute inset-0 flex items-center justify-center text-2xl font-semibold',
              scoreBandText[scoreBand(clamped)],
            )}
          >
            {clamped}
          </span>
        </div>
        <div className="grid gap-1">
          <p className="text-secondary text-sm">
            Your brand&apos;s visibility across LLMs for this run
          </p>
          <div className="flex items-center justify-center gap-2">
            <Badge variant="run-status" value={badge}>
              {label}
            </Badge>
          </div>
        </div>
        <dl className="border-border-subtle grid w-full grid-cols-2 gap-3 border-t pt-4 text-center">
          <div>
            <dt className="text-2xs text-muted font-mono font-medium tracking-[0.08em] uppercase">
              Completed
            </dt>
            <dd className="mono text-foreground text-lg font-semibold">
              {visibility.total_completed}
            </dd>
          </div>
          <div>
            <dt className="text-2xs text-muted font-mono font-medium tracking-[0.08em] uppercase">
              Failed
            </dt>
            <dd className="mono text-foreground text-lg font-semibold">
              {visibility.total_failed}
            </dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  );
}
