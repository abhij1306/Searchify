import { cn } from '@/lib/utils';

export type TrendPoint = {
  /** X-axis label (e.g. a run date). */
  label: string;
  /**
   * Y value 0–100, or `null` when the metric is UNAVAILABLE for this point. A
   * null value produces a chart GAP (no dot, no line segment through it) and is
   * announced as "unavailable" — never coerced to a misleading zero.
   */
  value: number | null;
  /**
   * Marker metadata for an analyzer/scoring version change AT this point: when
   * set, a dashed version-boundary marker is drawn and announced. Kept optional
   * so the primitive still renders a plain series when omitted.
   */
  versionChange?: {
    /** Short human note, e.g. "Scoring rule v2 applied". */
    note: string;
  } | null;
};

/**
 * TrendChart — line/area chart for the cross-run Visibility trend.
 *
 * The single chart owner for the `/visibility` Trends tab (do not add another).
 * It reuses the design-system visual language (accent fill-soft area + accent
 * stroke line + accent data dots) and stays token-only (bridged semantic
 * utilities, no raw hex) so reduced-motion and forced-color behavior come from
 * the design system.
 *
 * Behavior:
 *   - Deterministic 0–100 Y scaling (a Visibility Score / percentage is already
 *     0–100), independent of the data's own max, so two charts are comparable.
 *   - Valid empty rendering (no points) and single-point rendering (a lone dot,
 *     no misleading slope).
 *   - UNAVAILABLE points (`value: null`) become gaps: the line/area path splits
 *     across them, no numeric dot is drawn, and they are announced as
 *     "unavailable" (WCAG 1.4.1 — not conveyed by absence alone).
 *   - Accessible: an `img` role with a summary ARIA label describing the
 *     endpoints, plus `<title>` markers for each version-change boundary.
 */
export function TrendChart({
  data,
  width = 320,
  height = 120,
  label,
  className,
}: Readonly<{
  data: TrendPoint[];
  width?: number;
  height?: number;
  label?: string;
  className?: string;
}>) {
  const padding = 8;
  const innerWidth = width - padding * 2;
  const innerHeight = height - padding * 2;

  // Deterministic 0–100 domain: a percentage / Visibility Score is already on
  // that scale, so we never rescale to the data's own max (keeps charts
  // comparable and single points positioned truthfully).
  const DOMAIN_MAX = 100;
  const clamp = (v: number) => Math.max(0, Math.min(DOMAIN_MAX, v));
  const stepX = data.length > 1 ? innerWidth / (data.length - 1) : 0;

  // Geometry per point. A null value has no y (it is a gap): its x is still
  // reserved so the axis stays evenly spaced.
  const points = data.map((d, i) => {
    const x = data.length > 1 ? padding + i * stepX : width / 2;
    const y = d.value === null ? null : padding + innerHeight * (1 - clamp(d.value) / DOMAIN_MAX);
    return { x, y, value: d.value };
  });

  // Split the series into contiguous runs of available points; each run is its
  // own line/area sub-path so the line breaks across unavailable gaps.
  const segments: { x: number; y: number }[][] = [];
  let current: { x: number; y: number }[] = [];
  for (const p of points) {
    if (p.y === null) {
      if (current.length) segments.push(current);
      current = [];
    } else {
      current.push({ x: p.x, y: p.y });
    }
  }
  if (current.length) segments.push(current);

  const lineSegments = segments.filter((seg) => seg.length > 1);
  const areaSegments = segments.filter((seg) => seg.length > 1);

  const toLinePath = (seg: { x: number; y: number }[]) =>
    seg.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const toAreaPath = (seg: { x: number; y: number }[]) =>
    `${toLinePath(seg)} L${seg[seg.length - 1].x.toFixed(1)},${(height - padding).toFixed(1)} L${seg[0].x.toFixed(1)},${(height - padding).toFixed(1)} Z`;

  const valueText = (v: number | null) => (v === null ? 'unavailable' : `${v}`);
  const summary = !data.length
    ? 'No trend data'
    : data.length === 1
      ? `Single point ${data[0].label} (${valueText(data[0].value)})`
      : `Trend from ${data[0].label} (${valueText(data[0].value)}) to ${data[data.length - 1].label} (${valueText(data[data.length - 1].value)})`;
  const hasGap = data.some((d) => d.value === null);
  const gapNote = hasGap ? ' Some points are unavailable and shown as gaps.' : '';
  const ariaLabel = label ? `${label}: ${summary}${gapNote}` : `${summary}${gapNote}`;

  return (
    <svg
      role="img"
      aria-label={ariaLabel}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn('overflow-visible', className)}
    >
      <title>{ariaLabel}</title>
      {areaSegments.map((seg, i) => (
        <path key={`area-${i}`} d={toAreaPath(seg)} className="fill-accent-soft" />
      ))}
      {lineSegments.map((seg, i) => (
        <path
          key={`line-${i}`}
          d={toLinePath(seg)}
          fill="none"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="stroke-accent"
        />
      ))}
      {/* Version-boundary markers: a dashed vertical line + a warning dot, with
          a <title> so the change is announced (not conveyed by color alone). */}
      {points.map((p, i) =>
        data[i].versionChange ? (
          <g key={`marker-${data[i].label}`} data-version-marker="">
            <line
              x1={p.x}
              y1={padding}
              x2={p.x}
              y2={height - padding}
              strokeWidth={1}
              strokeDasharray="4 3"
              className="stroke-warning opacity-60"
              aria-hidden
            />
            <circle cx={p.x} cy={padding} r={3} className="fill-warning">
              <title>{`Version change at ${data[i].label}: ${data[i].versionChange?.note}`}</title>
            </circle>
          </g>
        ) : null,
      )}
      {/* Data dots: only for AVAILABLE points — a null value draws no dot. */}
      {points.map((p, i) =>
        p.y === null ? null : (
          <circle key={data[i].label} cx={p.x} cy={p.y} r={2.5} className="fill-accent">
            <title>{`${data[i].label}: ${data[i].value}`}</title>
          </circle>
        ),
      )}
    </svg>
  );
}
