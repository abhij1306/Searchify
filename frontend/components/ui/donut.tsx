import { cn } from '@/lib/utils';

export type DonutSegment = {
  label: string;
  value: number;
  /**
   * Bridged token stroke utility for this segment (e.g. 'stroke-citation-owned',
   * 'stroke-accent'). Token-only — callers never pass raw hex.
   */
  colorClass: string;
};

/**
 * Donut (§8) — segmented ring for per-engine / citation-share breakdowns.
 * Renders an SVG with one arc per segment plus a legend. The whole figure
 * carries an ARIA label summarising the shares (role="img").
 */
export function Donut({
  segments,
  size = 120,
  strokeWidth = 14,
  label,
  showLegend = true,
  centerLabel,
  className,
}: Readonly<{
  segments: DonutSegment[];
  size?: number;
  strokeWidth?: number;
  label?: string;
  showLegend?: boolean;
  centerLabel?: string;
  className?: string;
}>) {
  const total = segments.reduce((sum, s) => sum + Math.max(0, s.value), 0);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  const summary =
    total > 0
      ? segments.map((s) => `${s.label} ${Math.round((s.value / total) * 100)}%`).join(', ')
      : 'No data';
  const ariaLabel = label ? `${label}: ${summary}` : summary;

  let offsetAccumulator = 0;

  return (
    <div className={cn('flex items-center gap-4', className)}>
      <div
        className="relative inline-flex items-center justify-center"
        style={{ width: size, height: size }}
      >
        <svg
          role="img"
          aria-label={ariaLabel}
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="-rotate-90"
        >
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={strokeWidth}
            className="stroke-border-subtle"
          />
          {total > 0 &&
            segments.map((segment) => {
              const fraction = Math.max(0, segment.value) / total;
              const dash = fraction * circumference;
              const gap = circumference - dash;
              const dashOffset = -offsetAccumulator * circumference;
              offsetAccumulator += fraction;
              return (
                <circle
                  key={segment.label}
                  cx={size / 2}
                  cy={size / 2}
                  r={radius}
                  fill="none"
                  strokeWidth={strokeWidth}
                  strokeDasharray={`${dash} ${gap}`}
                  strokeDashoffset={dashOffset}
                  className={segment.colorClass}
                />
              );
            })}
        </svg>
        {centerLabel ? (
          <span
            aria-hidden
            className="mono text-foreground absolute inset-0 flex items-center justify-center text-sm font-semibold"
          >
            {centerLabel}
          </span>
        ) : null}
      </div>
      {showLegend ? (
        <ul className="flex flex-col gap-1.5">
          {segments.map((segment) => (
            <li key={segment.label} className="text-secondary flex items-center gap-2 text-xs">
              <svg aria-hidden width={10} height={10} viewBox="0 0 10 10">
                {/* Swatch reuses the segment's bridged stroke token (no raw hex,
                    and no runtime class string that Tailwind can't detect). */}
                <circle
                  cx={5}
                  cy={5}
                  r={4}
                  fill="none"
                  strokeWidth={2}
                  className={segment.colorClass}
                />
              </svg>
              <span className="text-foreground">{segment.label}</span>
              <span className="mono text-muted">
                {total > 0 ? `${Math.round((segment.value / total) * 100)}%` : '—'}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
