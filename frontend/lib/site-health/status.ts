/**
 * Site Health lifecycle / status presentation helpers (Task 2) — PURE.
 *
 * Maps the crawl overall/discovery/analysis sub-states, per-page analysis
 * states, and Free redaction rules onto display view-models the screens
 * (Tasks 7/8) render without embedding business logic. No transport, no React.
 *
 * Key product rules encoded here:
 *   - discovery counts are PROVISIONAL until discovery terminalizes ("N pages
 *     discovered so far" vs "N pages discovered");
 *   - Free sample mode never renders a total placeholder or count-dependent
 *     copy — `total_url_count` is null and there is no "discovered so far";
 *   - error / blocked rows are explicit states, never a fabricated zero score;
 *   - missing / not-yet-analysed scores render the `—` placeholder.
 */
import type {
  CrawlAnalysisStatus,
  CrawlDiscoveryStatus,
  CrawlOverallStatus,
  PageAnalysisStatus,
  SiteCrawl,
  SiteHealthEntitlement,
} from '@/lib/api/types';
import type { RunStatusValue, StatusValue } from '@/components/ui/badge-variants';

/** The not-yet-analysed / not-applicable placeholder (matches visibility UI). */
export const PLACEHOLDER = '—';

/** Overall crawl statuses that are terminal (stop polling). */
const TERMINAL_OVERALL: ReadonlySet<CrawlOverallStatus> = new Set<CrawlOverallStatus>([
  'completed',
  'partially_completed',
  'failed',
  'cancelled',
]);

/** Overall statuses at which a cooperative cancel is still meaningful. */
const CANCELABLE_OVERALL: ReadonlySet<CrawlOverallStatus> = new Set<CrawlOverallStatus>([
  'draft',
  'validating',
  'queued',
  'running',
]);

/** Discovery sub-states that are terminal. */
const TERMINAL_DISCOVERY: ReadonlySet<CrawlDiscoveryStatus> = new Set<CrawlDiscoveryStatus>([
  'completed',
  'sample_completed',
  'failed',
  'cancelled',
]);

/** Analysis sub-states that are terminal. */
const TERMINAL_ANALYSIS: ReadonlySet<CrawlAnalysisStatus> = new Set<CrawlAnalysisStatus>([
  'completed',
  'partially_completed',
  'failed',
  'cancelled',
]);

/** True while the crawl page should keep polling `GET /site-crawls/{id}`. */
export function shouldPollCrawl(crawl: Pick<SiteCrawl, 'status'>): boolean {
  return !TERMINAL_OVERALL.has(crawl.status);
}

/** True when the crawl can still be cancelled cooperatively. */
export function isCrawlCancelable(status: CrawlOverallStatus): boolean {
  return CANCELABLE_OVERALL.has(status);
}

export function isDiscoveryTerminal(status: CrawlDiscoveryStatus): boolean {
  return TERMINAL_DISCOVERY.has(status);
}

export function isAnalysisTerminal(status: CrawlAnalysisStatus): boolean {
  return TERMINAL_ANALYSIS.has(status);
}

/**
 * True while discovery counts are provisional (still running). A `sample_mode`
 * crawl is NEVER provisional — it never implies continued full-site scanning,
 * so it must never be checked into shared background-scanning copy without
 * this shared helper enforcing the rule itself (not left to each caller).
 */
export function isDiscoveryProvisional(
  crawl: Pick<SiteCrawl, 'sample_mode' | 'discovery_status' | 'inventory_complete'>,
): boolean {
  if (crawl.sample_mode) return false;
  return !crawl.inventory_complete && !TERMINAL_DISCOVERY.has(crawl.discovery_status);
}

/** True when the crawl is a Free server-selected sample crawl. */
export function isSampleMode(crawl: Pick<SiteCrawl, 'sample_mode'>): boolean {
  return crawl.sample_mode;
}

/**
 * Discovery-progress copy. Free sample mode NEVER renders a total or "so far"
 * language (no count side channel); Starter uses provisional "discovered so
 * far" until discovery terminalizes, then the settled "discovered".
 */
export function discoveryProgressLabel(
  crawl: Pick<SiteCrawl, 'sample_mode' | 'discovery_status' | 'inventory_complete' | 'visible_url_count'>,
): string {
  const n = crawl.visible_url_count;
  if (crawl.sample_mode) {
    return `${n} sample ${pluralize(n, 'page')}`;
  }
  if (isDiscoveryProvisional(crawl)) {
    return `${n} ${pluralize(n, 'page')} discovered so far`;
  }
  return `${n} ${pluralize(n, 'page')} discovered`;
}

/**
 * Whether a discovered/total count may be shown at all. Free (or any crawl the
 * entitlement redacts) hides the total entirely: the value is null on the wire
 * and no placeholder total is rendered.
 */
export function canShowDiscoveredTotal(
  entitlement: Pick<SiteHealthEntitlement, 'can_view_discovered_total'>,
  crawl: Pick<SiteCrawl, 'sample_mode' | 'total_url_count'>,
): boolean {
  return entitlement.can_view_discovered_total && !crawl.sample_mode && crawl.total_url_count !== null;
}

/** Which phase of the Site Health flow to render for the active crawl. */
export type SiteHealthPhase =
  | 'empty'
  | 'discovering'
  | 'selection'
  | 'analyzing'
  | 'dashboard'
  | 'terminal';

/**
 * Resolve the screen phase for the active crawl (discovery → selection →
 * analysis → dashboard). Terminal failures/cancellations WITHOUT score data get
 * an explicit 'terminal' phase — otherwise they would fall through to
 * 'selection'/'analyzing' and render an active-looking progress view (and
 * another Cancel button) for a crawl that has already stopped.
 */
export function resolveSiteHealthPhase(
  crawl: Pick<SiteCrawl, 'status' | 'discovery_status' | 'analysis_status' | 'score_summary'> | null,
  plan: SiteHealthEntitlement['plan_key'],
): SiteHealthPhase {
  if (!crawl) return 'empty';
  // Terminal crawls with score data → completed dashboard (even mid-analysis).
  if (['completed', 'partially_completed'].includes(crawl.status) || crawl.score_summary) {
    return 'dashboard';
  }
  if (crawl.status === 'failed' || crawl.status === 'cancelled') return 'terminal';
  // Discovery still running.
  if (!TERMINAL_DISCOVERY.has(crawl.discovery_status)) return 'discovering';
  // Discovery done. Free auto-analyzes its sample (no manual selection); Starter
  // stages a monitored set unless analysis has already started.
  if (crawl.analysis_status === 'running') return 'analyzing';
  if (plan === 'starter' && crawl.analysis_status === 'pending') return 'selection';
  return 'analyzing';
}

/** Map an overall crawl status onto a run-status badge value. */
export function crawlBadgeValue(status: CrawlOverallStatus): RunStatusValue {
  switch (status) {
    case 'validating':
      return 'queued';
    case 'partially_completed':
      return 'partial';
    default:
      return status;
  }
}

/** Map a per-page analysis status onto a status-badge value. */
export function pageStatusBadgeValue(status: PageAnalysisStatus): StatusValue {
  switch (status) {
    case 'completed':
      return 'success';
    case 'partially_completed':
      return 'warning';
    case 'failed':
    case 'error':
    case 'blocked':
    case 'cancelled':
      return 'danger';
    default:
      // not_selected / pending / running
      return 'info';
  }
}

/** True when a page row is an explicit error/blocked state (not a zero score). */
export function isErrorRow(status: PageAnalysisStatus): boolean {
  return (
    status === 'failed' || status === 'error' || status === 'blocked' || status === 'cancelled'
  );
}

/** Human-readable label for any snake_case lifecycle token. */
export function statusLabel(status: string): string {
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

/**
 * Format a 0–100 score for display. Null (not yet analysed) and NaN render the
 * `—` placeholder — an error/blocked row is NEVER shown as 0.
 */
export function formatScore(score: number | null): string {
  if (score === null || Number.isNaN(score)) return PLACEHOLDER;
  return `${Math.round(score * 10) / 10}`;
}

/** Format a nullable issue count; null (unanalysed) renders the placeholder. */
export function formatIssueCount(count: number | null): string {
  if (count === null) return PLACEHOLDER;
  return `${count}`;
}

/** Short, stable date/time label for a timestamp (or the placeholder). */
export function formatAudited(timestamp: string | null): string {
  if (!timestamp) return PLACEHOLDER;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function pluralize(n: number, word: string): string {
  return n === 1 ? word : `${word}s`;
}
