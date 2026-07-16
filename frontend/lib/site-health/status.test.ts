import { describe, expect, it } from 'vitest';

import {
  PLACEHOLDER,
  canShowDiscoveredTotal,
  crawlBadgeValue,
  discoveryProgressLabel,
  formatAudited,
  formatIssueCount,
  formatScore,
  isAnalysisTerminal,
  isCrawlCancelable,
  isDiscoveryProvisional,
  isDiscoveryTerminal,
  isErrorRow,
  isSampleMode,
  pageStatusBadgeValue,
  shouldPollCrawl,
  statusLabel,
} from './status';

describe('polling / cancel / terminal predicates', () => {
  it('polls while not terminal, stops when terminal', () => {
    expect(shouldPollCrawl({ status: 'running' })).toBe(true);
    expect(shouldPollCrawl({ status: 'queued' })).toBe(true);
    expect(shouldPollCrawl({ status: 'completed' })).toBe(false);
    expect(shouldPollCrawl({ status: 'partially_completed' })).toBe(false);
    expect(shouldPollCrawl({ status: 'cancelled' })).toBe(false);
  });

  it('is cancelable only before terminal', () => {
    expect(isCrawlCancelable('running')).toBe(true);
    expect(isCrawlCancelable('draft')).toBe(true);
    expect(isCrawlCancelable('completed')).toBe(false);
    expect(isCrawlCancelable('failed')).toBe(false);
  });

  it('recognises terminal discovery / analysis sub-states', () => {
    expect(isDiscoveryTerminal('sample_completed')).toBe(true);
    expect(isDiscoveryTerminal('running')).toBe(false);
    expect(isAnalysisTerminal('partially_completed')).toBe(true);
    expect(isAnalysisTerminal('pending')).toBe(false);
  });
});

describe('discovery provisional / sample-mode copy', () => {
  const base = {
    sample_mode: false,
    discovery_status: 'running' as const,
    inventory_complete: false,
    visible_url_count: 42,
  };

  it('is provisional while discovery runs and inventory is incomplete', () => {
    expect(isDiscoveryProvisional(base)).toBe(true);
  });

  it('is not provisional once inventory is complete', () => {
    expect(isDiscoveryProvisional({ ...base, inventory_complete: true })).toBe(false);
  });

  it('renders "discovered so far" while provisional (Starter)', () => {
    expect(discoveryProgressLabel(base)).toBe('42 pages discovered so far');
  });

  it('renders settled "discovered" once complete', () => {
    expect(
      discoveryProgressLabel({ ...base, discovery_status: 'completed', inventory_complete: true }),
    ).toBe('42 pages discovered');
  });

  it('renders sample copy for Free (never a total or "so far")', () => {
    const sample = { ...base, sample_mode: true, inventory_complete: true, visible_url_count: 10 };
    expect(isSampleMode(sample)).toBe(true);
    expect(discoveryProgressLabel(sample)).toBe('10 sample pages');
    expect(discoveryProgressLabel(sample)).not.toContain('so far');
    expect(discoveryProgressLabel(sample)).not.toContain('discovered');
  });

  it('pluralizes a single page', () => {
    expect(discoveryProgressLabel({ ...base, visible_url_count: 1, inventory_complete: true, discovery_status: 'completed' })).toBe(
      '1 page discovered',
    );
  });
});

describe('canShowDiscoveredTotal (Free redaction rendering input)', () => {
  it('shows the total for a Starter crawl with a real total', () => {
    expect(
      canShowDiscoveredTotal(
        { can_view_discovered_total: true },
        { sample_mode: false, total_url_count: 25000 },
      ),
    ).toBe(true);
  });

  it('hides the total when the entitlement redacts it (Free)', () => {
    expect(
      canShowDiscoveredTotal(
        { can_view_discovered_total: false },
        { sample_mode: true, total_url_count: null },
      ),
    ).toBe(false);
  });

  it('hides the total for a sample crawl even if the flag is on', () => {
    expect(
      canShowDiscoveredTotal(
        { can_view_discovered_total: true },
        { sample_mode: true, total_url_count: null },
      ),
    ).toBe(false);
  });

  it('hides the total while it is still null (provisional)', () => {
    expect(
      canShowDiscoveredTotal(
        { can_view_discovered_total: true },
        { sample_mode: false, total_url_count: null },
      ),
    ).toBe(false);
  });
});

describe('badge mapping', () => {
  it('maps overall crawl status to a run-status badge value', () => {
    expect(crawlBadgeValue('validating')).toBe('queued');
    expect(crawlBadgeValue('partially_completed')).toBe('partial');
    expect(crawlBadgeValue('running')).toBe('running');
  });

  it('maps page analysis status to a status badge value', () => {
    expect(pageStatusBadgeValue('completed')).toBe('success');
    expect(pageStatusBadgeValue('partially_completed')).toBe('warning');
    expect(pageStatusBadgeValue('failed')).toBe('danger');
    expect(pageStatusBadgeValue('blocked')).toBe('danger');
    expect(pageStatusBadgeValue('pending')).toBe('info');
    expect(pageStatusBadgeValue('not_selected')).toBe('info');
  });

  it('classifies error/blocked rows explicitly (not zero scores)', () => {
    expect(isErrorRow('failed')).toBe(true);
    expect(isErrorRow('blocked')).toBe(true);
    expect(isErrorRow('cancelled')).toBe(true);
    expect(isErrorRow('completed')).toBe(false);
  });
});

describe('score / count / date placeholders', () => {
  it('renders — for a null or NaN score (never 0 for missing)', () => {
    expect(formatScore(null)).toBe(PLACEHOLDER);
    expect(formatScore(Number.NaN)).toBe(PLACEHOLDER);
    expect(formatScore(0)).toBe('0');
    expect(formatScore(88.25)).toBe('88.3');
  });

  it('renders — for a null issue count', () => {
    expect(formatIssueCount(null)).toBe(PLACEHOLDER);
    expect(formatIssueCount(0)).toBe('0');
    expect(formatIssueCount(4)).toBe('4');
  });

  it('renders — for a null last-audited', () => {
    expect(formatAudited(null)).toBe(PLACEHOLDER);
    expect(formatAudited('not-a-date')).toBe('not-a-date');
  });

  it('titleizes snake_case status tokens', () => {
    expect(statusLabel('sample_completed')).toBe('Sample Completed');
    expect(statusLabel('running')).toBe('Running');
  });
});
