/**
 * Score-band mapping (§ score bands): low 0–24, mid 25–49, good 50–74,
 * high 75–100. Returns the bridged token utility classes for stroke/text
 * so data-viz primitives stay token-only (no raw hex). */
export type ScoreBand = 'low' | 'mid' | 'good' | 'high';

export function scoreBand(score: number): ScoreBand {
  if (score >= 75) return 'high';
  if (score >= 50) return 'good';
  if (score >= 25) return 'mid';
  return 'low';
}

export const scoreBandStroke: Record<ScoreBand, string> = {
  low: 'stroke-score-low',
  mid: 'stroke-score-mid',
  good: 'stroke-score-good',
  high: 'stroke-score-high',
};

export const scoreBandText: Record<ScoreBand, string> = {
  low: 'text-score-low',
  mid: 'text-score-mid',
  good: 'text-score-good',
  high: 'text-score-high',
};

/**
 * Null-aware text class for a score cell: muted for a missing score (which
 * renders the `—` placeholder), the band colour otherwise. Shared by every
 * score table so the missing-score treatment never diverges.
 */
export function scoreTextClass(score: number | null): string {
  if (score === null) return 'text-muted';
  return scoreBandText[scoreBand(score)];
}
