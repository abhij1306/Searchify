/**
 * LLM Analytics toolbar vocabulary (F8): the date-range presets and snapshot
 * granularity driving the `/analytics` screen.
 *
 * The range presets + `rangeToFrom` resolution are OWNED by
 * `@/lib/visibility/trends` (one concept → one owner, invariant 2); this
 * module re-exports them so the analytics surface has a single import point,
 * and adds the analytics-specific granularity vocabulary (`day | week |
 * month` — the backend `snapshotGranularitySchema`, NOT the visibility
 * trend's `run | week | month`) plus the bucket-count labels the cards use.
 */
import type { z } from 'zod';

import type { snapshotGranularitySchema } from '@/lib/api/schemas';
import { bucketAdjective } from '@/lib/format';

export {
  RANGE_OPTIONS,
  rangeLabel,
  rangeToFrom,
  type TrendRange as AnalyticsRange,
} from '@/lib/visibility/trends';

// The granularity options + adjective form are OWNED by `@/lib/format`
// (shared with the traffic surface, invariant 2) — re-exported here.
export { bucketAdjective, GRANULARITY_OPTIONS } from '@/lib/format';

/** Snapshot bucket granularity — mirrors the backend contract vocabulary. */
export type AnalyticsGranularity = z.infer<typeof snapshotGranularitySchema>;

/** Capitalized adjective for sentence-start copy ("Weekly visibility score…"). */
export function bucketAdjectiveTitle(granularity: AnalyticsGranularity): string {
  const adjective = bucketAdjective(granularity);
  return adjective.charAt(0).toUpperCase() + adjective.slice(1);
}

/** Bucket-count badge label ("13 weeks", "1 day"). */
export function bucketCountLabel(granularity: AnalyticsGranularity, count: number): string {
  const noun = granularity === 'day' ? 'day' : granularity === 'week' ? 'week' : 'month';
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}
