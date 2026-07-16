import { cn } from '@/lib/utils';
import { scoreBand, scoreBandStroke, scoreBandText } from './score-band';

/**
 * ScoreRing (§8) — circular progress. Color from the score-band token, center
 * shows the mono display number. Carries an ARIA label with the percentage
 * (role="img") so the value is announced to assistive tech.
 */
export function ScoreRing({
  value,
  size = 96,
  strokeWidth = 8,
  label,
  showValue = true,
  className,
}: Readonly<{
  /** Score 0–100. */
  value: number;
  size?: number;
  strokeWidth?: number;
  /** Accessible label; defaults to "Visibility score: N%". */
  label?: string;
  showValue?: boolean;
  className?: string;
}>) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  const band = scoreBand(clamped);
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - clamped / 100);
  const ariaLabel = label ?? `Visibility score: ${clamped}%`;

  return (
    <div
      className={cn('relative inline-flex items-center justify-center', className)}
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
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          className={cn('transition-[stroke-dashoffset]', scoreBandStroke[band])}
        />
      </svg>
      {showValue ? (
        <span
          aria-hidden
          className={cn(
            'mono absolute inset-0 flex items-center justify-center text-lg font-semibold',
            scoreBandText[band],
          )}
        >
          {clamped}
        </span>
      ) : null}
    </div>
  );
}
