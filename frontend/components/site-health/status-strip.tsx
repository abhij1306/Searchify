'use client';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Label, Metric } from '@/components/ui/typography';
import type { PageSummary, SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';
import {
  PLACEHOLDER,
  canShowDiscoveredTotal,
  crawlBadgeValue,
  dashboardRunNotice,
  discoveryProgressLabel,
  isDiscoveryProvisional,
  statusLabel,
  type SiteHealthPhase,
} from '@/lib/site-health/status';

/**
 * Always-mounted status/progress strip of the canonical Site Health screen.
 *
 * One region that narrates the whole lifecycle in place — discovery counts
 * while discovering, selection guidance once discovery stops, analysis
 * progress counters while auditing, and the run-outcome notice
 * (`dashboardRunNotice`) for a run that did not complete cleanly. The strip
 * changes CONTENT, never the screen: no phase mounts or unmounts the region.
 * Free redaction rules apply — sample crawls never imply a hidden total.
 */
export function StatusStrip({
  crawl,
  phase,
  entitlement,
  cancelPending,
  crawlStarting,
  pages,
  selectedTotal,
  selectedError,
}: Readonly<{
  crawl: SiteCrawl | null;
  phase: SiteHealthPhase;
  entitlement: SiteHealthEntitlement;
  cancelPending: boolean;
  /** A fresh crawl create is in flight — freeze current content behind a notice. */
  crawlStarting: boolean;
  /** Bounded monitored-page window (observed "running" rows only, never totals). */
  pages: PageSummary[];
  /** This project's active monitored count; null until loaded. */
  selectedTotal: number | null;
  /** True when the monitored-count fetch failed (counts fall back, noted here). */
  selectedError: boolean;
}>) {
  // The wrapper (and its test id) stays mounted in every phase — only the
  // CONTENT below changes. The screen regression tests assert this stability.
  return (
    <div className="grid gap-2 empty:hidden" data-testid="status-strip">
      <StripContent
        crawl={crawl}
        phase={phase}
        entitlement={entitlement}
        cancelPending={cancelPending}
        crawlStarting={crawlStarting}
        pages={pages}
        selectedTotal={selectedTotal}
        selectedError={selectedError}
      />
    </div>
  );
}

function StripContent({
  crawl,
  phase,
  entitlement,
  cancelPending,
  crawlStarting,
  pages,
  selectedTotal,
  selectedError,
}: Readonly<{
  crawl: SiteCrawl | null;
  phase: SiteHealthPhase;
  entitlement: SiteHealthEntitlement;
  cancelPending: boolean;
  crawlStarting: boolean;
  pages: PageSummary[];
  selectedTotal: number | null;
  selectedError: boolean;
}>) {
  if (crawlStarting) {
    return (
      <Alert tone="info">
        {crawl
          ? 'Starting a fresh crawl — your results and monitored selection stay in view until the new run takes over.'
          : 'Starting discovery — pages will appear below as they are found.'}
      </Alert>
    );
  }

  if (!crawl || phase === 'empty') {
    return (
      <Card>
        <CardContent className="py-6 text-center">
          <p className="text-secondary text-sm">
            Discover and analyze your site&apos;s pages for AI search optimization. Start a
            discovery to see your pages, scores, and issues here — this screen updates in place as
            the crawl progresses.
          </p>
        </CardContent>
      </Card>
    );
  }

  if (phase === 'discovering') {
    return (
      <DiscoveryStrip crawl={crawl} entitlement={entitlement} cancelPending={cancelPending} />
    );
  }

  if (phase === 'analyzing') {
    return (
      <AnalysisStrip
        crawl={crawl}
        cancelPending={cancelPending}
        pages={pages}
        selectedTotal={selectedTotal}
        selectedError={selectedError}
      />
    );
  }

  if (phase === 'selection') {
    return (
      <Alert tone="info">
        {crawl.status === 'cancelled'
          ? 'Discovery was cancelled — the pages found so far are kept below. Select the pages to monitor, save your selection, then start the analysis.'
          : 'Discovery finished. Select the pages to monitor below, then start the analysis — results will appear on this screen.'}
      </Alert>
    );
  }

  if (phase === 'terminal') {
    return (
      <Alert tone={crawl.status === 'failed' ? 'danger' : 'info'}>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="run-status" value={crawlBadgeValue(crawl.status)}>
            {statusLabel(crawl.status)}
          </Badge>
          <span>
            {crawl.status === 'cancelled'
              ? 'This crawl was cancelled before it produced results.'
              : crawl.error_message || 'This crawl failed before it produced results.'}
          </span>
        </div>
      </Alert>
    );
  }

  // phase === 'dashboard': quiet unless the run did not complete cleanly.
  const runNotice = dashboardRunNotice(crawl);
  if (!runNotice) return null;
  return (
    <Alert tone={runNotice.tone}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="run-status" value={runNotice.badge}>
          {statusLabel(crawl.status)}
        </Badge>
        <span>{runNotice.message}</span>
      </div>
    </Alert>
  );
}

function DiscoveryStrip({
  crawl,
  entitlement,
  cancelPending,
}: Readonly<{
  crawl: SiteCrawl;
  entitlement: SiteHealthEntitlement;
  cancelPending: boolean;
}>) {
  const provisional = isDiscoveryProvisional(crawl);
  const showTotal = canShowDiscoveredTotal(entitlement, crawl);
  const isFree = entitlement.plan_key === 'free';
  const progressCopy = provisional
    ? `${discoveryProgressLabel(crawl)} — scanning continues in the background`
    : discoveryProgressLabel(crawl);

  return (
    <Card>
      <CardContent className="grid gap-5">
        <div className="flex items-center gap-3">
          <Badge variant="run-status" value={crawlBadgeValue(crawl.status)}>
            {statusLabel(crawl.status)}
          </Badge>
          <span className="text-secondary text-sm" aria-live="polite">
            {cancelPending
              ? 'Cancelling discovery — finishing the page in flight and stopping'
              : progressCopy}
          </span>
        </div>

        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div className="grid gap-1">
            <Label>{isFree ? 'Sample URLs' : 'URLs found'}</Label>
            <Metric className="text-2xl">{crawl.visible_url_count}</Metric>
          </div>
          {showTotal && crawl.total_url_count !== null ? (
            <div className="grid gap-1">
              <Label>Total discovered</Label>
              <Metric className="text-2xl">{crawl.total_url_count}</Metric>
            </div>
          ) : null}
          <div className="grid gap-1">
            <Label>Discovery</Label>
            <span className="text-secondary text-sm">{statusLabel(crawl.discovery_status)}</span>
          </div>
        </dl>

        {isFree ? (
          <div className="border-warning-border bg-warning-bg text-warning-text rounded-md border p-3 text-sm">
            <p className="font-medium">
              Free plan — we&apos;ll automatically analyze a {entitlement.sample_url_limit}-page
              sample of your site.
            </p>
            <p className="text-warning-text/90 mt-0.5">
              Upgrade to Starter to choose which pages to monitor.
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function AnalysisStrip({
  crawl,
  cancelPending,
  pages,
  selectedTotal,
  selectedError,
}: Readonly<{
  crawl: SiteCrawl;
  cancelPending: boolean;
  pages: PageSummary[];
  selectedTotal: number | null;
  selectedError: boolean;
}>) {
  const summary = crawl.score_summary;
  // Fallbacks while the crawl is still running: `score_summary` is only
  // written when the crawl terminalizes, so derive the live view from server
  // counters instead of rendering 0s. `pages.length` is the last resort (a
  // bounded window, but better than nothing before the quota loads).
  const selected = summary?.selected_count ?? selectedTotal ?? pages.length;
  const completed = summary?.analyzed_count ?? crawl.analyzed_count;
  const failed = crawl.failed_count;
  // `completed`/`failed` use the server-aggregated crawl counters. `running`
  // is observed from the visible window (no server counter exists for it),
  // and `queued` is the arithmetic remainder — clamped at 0 so a transiently
  // stale mix of counters can never render a negative count. Until the
  // selected total is known, `Queued: 0` would misread as "nothing left to
  // do", so it renders the placeholder instead.
  const countsKnown = summary !== null || selectedTotal !== null;
  const running = pages.filter((p) => p.analysis_status === 'running').length;
  const queued = countsKnown ? Math.max(0, selected - completed - failed - running) : null;

  return (
    <Card>
      <CardContent className="grid gap-5">
        <div className="flex items-center gap-3">
          <Badge variant="run-status" value={crawlBadgeValue(crawl.status)}>
            {statusLabel(crawl.status)}
          </Badge>
          <span className="text-secondary text-sm" aria-live="polite">
            {cancelPending
              ? 'Cancelling analysis — finishing the page in flight and stopping'
              : 'Auditing selected pages for technical and AEO health issues'}
          </span>
        </div>

        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <CountCell label="Total pages" value={countsKnown ? selected : null} />
          <CountCell label="Completed" value={completed} className="text-run-completed" />
          <CountCell label="In progress" value={running} className="text-run-running" />
          <CountCell label="Queued" value={queued} className="text-muted" />
        </dl>

        {selectedError ? (
          <Alert tone="warning">
            Could not load the selected-page count — progress totals may be approximate until it
            refreshes.
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

function CountCell({
  label,
  value,
  className,
}: Readonly<{ label: string; value: number | null; className?: string }>) {
  return (
    <div className="grid gap-1">
      <Label>{label}</Label>
      <Metric className={`text-xl ${className ?? ''}`}>{value ?? PLACEHOLDER}</Metric>
    </div>
  );
}
