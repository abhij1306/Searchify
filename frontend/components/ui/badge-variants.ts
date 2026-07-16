/**
 * Badge token maps (§8). Each family maps a value → bridged semantic token
 * classes (bg + text + border). No raw hex; all classes resolve to the
 * `@theme inline` bridge in globals.css.
 *
 * Families:
 *  - status:         success | warning | danger | info
 *  - sentiment:      positive | neutral | negative
 *  - classification: owned | competitor | third-party  (citation classification)
 *  - run-status:     draft | queued | running | analyzing | completed | partial | failed | cancelled
 *  - neutral:        the default grey chip
 */

export const statusBadge = {
  success: 'bg-success-bg text-success-text border-success-border',
  warning: 'bg-warning-bg text-warning-text border-warning-border',
  danger: 'bg-danger-bg text-danger-text border-danger-border',
  info: 'bg-info-bg text-info-text border-info-border',
} as const;

export const sentimentBadge = {
  positive: 'bg-sentiment-positive-bg text-sentiment-positive-text border-transparent',
  neutral: 'bg-sentiment-neutral-bg text-sentiment-neutral-text border-transparent',
  negative: 'bg-sentiment-negative-bg text-sentiment-negative-text border-transparent',
} as const;

export const classificationBadge = {
  owned: 'bg-citation-owned-bg text-citation-owned-text border-transparent',
  competitor: 'bg-citation-competitor-bg text-citation-competitor-text border-transparent',
  'third-party': 'bg-citation-third-party-bg text-citation-third-party-text border-transparent',
} as const;

export const runStatusBadge = {
  draft: 'bg-run-draft-bg text-run-draft border-transparent',
  queued: 'bg-run-queued-bg text-run-queued border-transparent',
  running: 'bg-run-running-bg text-run-running border-transparent',
  analyzing: 'bg-run-analyzing-bg text-run-analyzing border-transparent',
  completed: 'bg-run-completed-bg text-run-completed border-transparent',
  partial: 'bg-run-partial-bg text-run-partial border-transparent',
  failed: 'bg-run-failed-bg text-run-failed border-transparent',
  cancelled: 'bg-run-cancelled-bg text-run-cancelled border-transparent',
} as const;

export const neutralBadge = 'bg-neutral-bg text-secondary border-transparent';

export type StatusValue = keyof typeof statusBadge;
export type SentimentValue = keyof typeof sentimentBadge;
export type ClassificationValue = keyof typeof classificationBadge;
export type RunStatusValue = keyof typeof runStatusBadge;

/** Shared pill shape/typography for every badge family. */
export const badgeBase =
  'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-2xs font-semibold leading-[1.4] tracking-wide capitalize';
