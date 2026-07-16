import { cn } from '@/lib/utils';

export type TrendPoint = {
  /** X-axis label (e.g. a run date). */
  label: string;
  /** Y value 0–100. */
  value: number;
};

/**
 * TrendChart — line/area chart for cross-run trend.
 *
 * ⚠️ BUILT BUT INTENTIONALLY UNUSED IN THE MVP UI. Cross-run trend is a
 * roadmap feature; this primitive is kept in the library (with a render+ARIA
 * unit test) so the future trend view has it ready. Do NOT wire it into any
 * MVP screen.
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

  const max = data.length ? Math.max(...data.map((d) => d.value), 1) : 1;
  const stepX = data.length > 1 ? innerWidth / (data.length - 1) : 0;

  const points = data.map((d, i) => {
    const x = padding + i * stepX;
    const y = padding + innerHeight * (1 - d.value / max);
    return { x, y };
  });

  const linePath = points
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
    .join(' ');

  const areaPath = points.length
    ? `${linePath} L${points[points.length - 1].x.toFixed(1)},${(height - padding).toFixed(1)} L${points[0].x.toFixed(1)},${(height - padding).toFixed(1)} Z`
    : '';

  const summary = data.length
    ? `Trend from ${data[0].label} (${data[0].value}) to ${data[data.length - 1].label} (${data[data.length - 1].value})`
    : 'No trend data';
  const ariaLabel = label ? `${label}: ${summary}` : summary;

  return (
    <svg
      role="img"
      aria-label={ariaLabel}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn('overflow-visible', className)}
    >
      {areaPath ? <path d={areaPath} className="fill-accent-soft" /> : null}
      {linePath ? (
        <path
          d={linePath}
          fill="none"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="stroke-accent"
        />
      ) : null}
      {points.map((p, i) => (
        <circle key={data[i].label} cx={p.x} cy={p.y} r={2.5} className="fill-accent" />
      ))}
    </svg>
  );
}
