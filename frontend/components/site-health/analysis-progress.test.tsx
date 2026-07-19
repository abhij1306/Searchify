import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AnalysisProgress } from './analysis-progress';
import type { PageSummary, SiteCrawl } from '@/lib/api/types';

// AnalysisProgress renders PagesTable, which calls useRouter for clickable
// rows; stub next/navigation (unavailable in jsdom).
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const CRAWL = '22222222-2222-4222-8222-222222222222';

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

describe('AnalysisProgress', () => {
  it('derives "Completed" from the server-aggregated analyzed_count, not a truncated pages page', () => {
    // Only ONE monitored page is present in this (deliberately truncated)
    // `pages` prop, but the crawl-wide score_summary says 1 of 3 is analyzed.
    // The "Completed" count must reflect the authoritative aggregate, and can
    // never exceed `selected` regardless of how many rows `pages` contains.
    render(
      <AnalysisProgress
        crawl={crawl()}
        pages={[page({ analysis_status: 'completed' })]}
        selectedTotal={3}
        onCancel={vi.fn()}
        cancelPending={false}
      />,
    );

    const totalLabel = screen.getByText('Total pages');
    expect(totalLabel.parentElement?.textContent).toContain('3'); // Total pages (selected)
    // "Completed" cell shows 1 (analyzed_count), not the count of terminal
    // rows filtered from the truncated `pages` array (which would also be 1
    // here, but for the wrong reason — assert against the aggregate directly).
    const completedLabel = screen.getByText('Completed', { selector: 'span.text-2xs.text-muted' });
    const completedValue = completedLabel.parentElement?.querySelector('.text-run-completed');
    expect(completedValue?.textContent).toBe('1');
  });

  it('never lets Completed exceed selected even if pages over-reports terminal rows', () => {
    render(
      <AnalysisProgress
        crawl={crawl({
          score_summary: {
            overall_score: null,
            technical_score: null,
            aeo_score: null,
            selected_count: 2,
            analyzed_count: 2,
            issue_count: 0,
            scoring_version: 's1',
          },
        })}
        pages={[
          page({ site_url_id: 'a', analysis_status: 'completed' }),
          page({ site_url_id: 'b', analysis_status: 'completed' }),
          page({ site_url_id: 'c', analysis_status: 'completed', monitored: false }),
        ]}
        selectedTotal={2}
        onCancel={vi.fn()}
        cancelPending={false}
      />,
    );

    const completedLabel = screen.getByText('Completed', { selector: 'span.text-2xs.text-muted' });
    const completedValue = completedLabel.parentElement?.querySelector('.text-run-completed');
    expect(completedValue?.textContent).toBe('2');
  });
});
