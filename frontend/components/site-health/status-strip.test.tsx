import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { StatusStrip } from './status-strip';
import type { PageSummary, SiteCrawl, SiteHealthEntitlement } from '@/lib/api/types';

const CRAWL = '22222222-2222-4222-8222-222222222222';

const entitlement: SiteHealthEntitlement = {
  workspace_id: '33333333-3333-4333-8333-333333333333',
  plan_key: 'starter',
  access_mode: 'selection',
  sample_url_limit: 10,
  monitored_url_limit: 50,
  can_view_discovered_total: true,
  capability_revision: 1,
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
};

function page(overrides: Partial<PageSummary> = {}): PageSummary {
  return {
    site_url_id: '11111111-1111-4111-8111-111111111111',
    crawl_id: CRAWL,
    normalized_url: 'https://acme.com/',
    display_url: 'https://acme.com/',
    title: 'Homepage',
    monitored: true,
    analysis_status: 'completed',
    error_code: '',
    issue_count: 3,
    technical_score: 46,
    aeo_score: 64,
    overall_score: 55,
    last_audited: '2026-07-16T00:00:00Z',
    page_type: 'article',
    ...overrides,
  };
}

function crawl(overrides: Partial<SiteCrawl> = {}): SiteCrawl {
  return {
    id: CRAWL,
    workspace_id: '33333333-3333-4333-8333-333333333333',
    project_id: '44444444-4444-4444-8444-444444444444',
    profile_id: '55555555-5555-4555-8555-555555555555',
    status: 'running',
    discovery_status: 'completed',
    analysis_status: 'running',
    root_url: 'https://acme.com/',
    sample_mode: false,
    seed: '1',
    inventory_complete: true,
    visible_url_count: 3,
    analyzed_count: 1,
    failed_count: 0,
    discovered_count: 3,
    total_url_count: 3,
    has_more_site_urls: false,
    score_summary: {
      overall_score: null,
      technical_score: null,
      aeo_score: null,
      selected_count: 3,
      analyzed_count: 1,
      issue_count: 0,
      scoring_version: 's1',
      by_page_type: {},
    },
    extractor_version: 'e1',
    analyzer_version: 'a1',
    rule_version: 'r1',
    scoring_version: 's1',
    error_message: '',
    created_at: '2026-07-16T00:00:00Z',
    updated_at: '2026-07-16T00:00:00Z',
    started_at: '2026-07-16T00:00:00Z',
    completed_at: null,
    ...overrides,
  };
}

function renderStrip(props: Partial<Parameters<typeof StatusStrip>[0]> = {}) {
  return render(
    <StatusStrip
      crawl={crawl()}
      phase="analyzing"
      entitlement={entitlement}
      cancelPending={false}
      crawlStarting={false}
      pages={[]}
      selectedTotal={null}
      selectedError={false}
      {...props}
    />,
  );
}

describe('StatusStrip — analysis counters', () => {
  it('derives "Completed" from the server-aggregated analyzed_count, not a truncated pages window', () => {
    // Only ONE monitored page is present in this (deliberately truncated)
    // `pages` prop, but the crawl-wide score_summary says 1 of 3 is analyzed.
    // The "Completed" count must reflect the authoritative aggregate.
    renderStrip({ pages: [page({ analysis_status: 'completed' })], selectedTotal: 3 });

    const totalLabel = screen.getByText('Total pages');
    expect(totalLabel.parentElement?.textContent).toContain('3');
    const completedLabel = screen.getByText('Completed');
    const completedValue = completedLabel.parentElement?.querySelector('.text-run-completed');
    expect(completedValue?.textContent).toBe('1');
  });

  it('shows — for Queued (not a false 0) while the selected total is unknown', () => {
    // No terminal score_summary yet AND the per-project monitored count has not
    // loaded (selectedTotal=null): the total is genuinely unknown, so Queued
    // must render the em-dash placeholder rather than a misleading 0.
    renderStrip({ crawl: crawl({ score_summary: null, analyzed_count: 0 }), selectedTotal: null });

    const queuedLabel = screen.getByText('Queued');
    expect(queuedLabel.parentElement?.textContent).toContain('—');
    const totalLabel = screen.getByText('Total pages');
    expect(totalLabel.parentElement?.textContent).toContain('—');
  });

  it('shows a real Queued count once the selected total is known', () => {
    renderStrip({
      crawl: crawl({ score_summary: null, analyzed_count: 1, failed_count: 0 }),
      pages: [page({ analysis_status: 'running' })],
      selectedTotal: 5,
    });

    // selected(5) - completed(1) - failed(0) - running(1) = 3 queued.
    const queuedLabel = screen.getByText('Queued');
    const queuedValue = queuedLabel.parentElement?.querySelector('.mono');
    expect(queuedValue?.textContent).toBe('3');
  });

  it('surfaces a monitored-count fetch error instead of silently approximating', () => {
    renderStrip({ crawl: crawl({ score_summary: null }), selectedError: true });

    expect(screen.getByText(/Could not load the selected-page count/)).toBeInTheDocument();
  });
});

describe('StatusStrip — lifecycle content', () => {
  it('narrates discovery with provisional Starter copy while scanning', () => {
    renderStrip({
      phase: 'discovering',
      crawl: crawl({
        status: 'running',
        discovery_status: 'running',
        analysis_status: 'pending',
        inventory_complete: false,
        score_summary: null,
      }),
    });

    expect(screen.getByText(/3 pages discovered so far/)).toBeInTheDocument();
  });

  it('freezes behind a starting notice while a fresh crawl create is in flight', () => {
    // The post-"Start analysis" window: the old crawl's phase must not flash
    // back into view (the reported bounce) — a single notice covers it.
    renderStrip({ crawlStarting: true, phase: 'selection', crawl: crawl({ status: 'cancelled' }) });

    expect(screen.getByText(/Starting a fresh crawl/)).toBeInTheDocument();
    expect(screen.queryByText(/Discovery was cancelled/)).not.toBeInTheDocument();
  });

  it('keeps the strip container mounted in every phase (canonical-screen invariant)', () => {
    const { rerender } = renderStrip({ phase: 'empty', crawl: null });
    const strip = screen.getByTestId('status-strip');

    for (const [phase, c] of [
      ['discovering', crawl({ discovery_status: 'running', score_summary: null })],
      ['selection', crawl({ status: 'cancelled', score_summary: null })],
      ['analyzing', crawl({ score_summary: null })],
      ['dashboard', crawl({ status: 'completed' })],
      ['terminal', crawl({ status: 'failed', score_summary: null })],
    ] as const) {
      rerender(
        <StatusStrip
          crawl={c}
          phase={phase}
          entitlement={entitlement}
          cancelPending={false}
          crawlStarting={false}
          pages={[]}
          selectedTotal={null}
          selectedError={false}
        />,
      );
      expect(screen.getByTestId('status-strip')).toBe(strip);
    }
  });
});
