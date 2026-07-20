import { describe, expect, it } from 'vitest';

import {
  PLACEHOLDER,
  canShowDiscoveredTotal,
  crawlBadgeValue,
  dashboardRunNotice,
  discoveryProgressLabel,
  formatAudited,
  formatIssueCount,
  formatScore,
  hasScoreData,
  inventoryModeForPhase,
  isAnalysisTerminal,
  isCrawlCancelable,
  isDiscoveryProvisional,
  isDiscoveryTerminal,
  isErrorRow,
  isSampleMode,
  pageStatusBadgeValue,
  primaryActionForPhase,
  resolveSiteHealthPhase,
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

  it('is NEVER provisional for a sample-mode crawl, even mid-discovery', () => {
    // Free sample discovery must never imply continued full-site scanning —
    // the shared helper enforces this directly rather than leaving it to
    // every caller to remember to check `sample_mode` first.
    expect(isDiscoveryProvisional({ ...base, sample_mode: true })).toBe(false);
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
    expect(
      discoveryProgressLabel({
        ...base,
        visible_url_count: 1,
        inventory_complete: true,
        discovery_status: 'completed',
      }),
    ).toBe('1 page discovered');
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

describe('resolveSiteHealthPhase', () => {
  const base = {
    status: 'running' as const,
    discovery_status: 'running' as const,
    analysis_status: 'pending' as const,
    score_summary: null,
    visible_url_count: 42,
  };

  it('resolves the active flow phases', () => {
    expect(resolveSiteHealthPhase(null, 'starter')).toBe('empty');
    expect(resolveSiteHealthPhase(base, 'starter')).toBe('discovering');
    expect(resolveSiteHealthPhase({ ...base, discovery_status: 'completed' }, 'starter')).toBe(
      'selection',
    );
    expect(
      resolveSiteHealthPhase(
        { ...base, discovery_status: 'completed', analysis_status: 'running' },
        'starter',
      ),
    ).toBe('analyzing');
    expect(resolveSiteHealthPhase({ ...base, status: 'completed' }, 'starter')).toBe('dashboard');
  });

  it('keeps a cancelled Starter crawl with discovered URLs in the selection phase', () => {
    // Discovered URLs persist through a cancel — the inventory must stay
    // reachable so the user can select pages to analyze, not a dead-end card.
    expect(
      resolveSiteHealthPhase(
        {
          ...base,
          status: 'cancelled',
          discovery_status: 'cancelled',
          analysis_status: 'cancelled',
        },
        'starter',
      ),
    ).toBe('selection');
  });

  it('is terminal for a cancelled crawl with nothing discovered', () => {
    expect(
      resolveSiteHealthPhase(
        {
          ...base,
          status: 'cancelled',
          discovery_status: 'cancelled',
          analysis_status: 'cancelled',
          visible_url_count: 0,
        },
        'starter',
      ),
    ).toBe('terminal');
  });

  it('is terminal for a cancelled Free crawl (no selection capability)', () => {
    expect(
      resolveSiteHealthPhase(
        {
          ...base,
          status: 'cancelled',
          discovery_status: 'cancelled',
          analysis_status: 'cancelled',
        },
        'free',
      ),
    ).toBe('terminal');
  });

  it('stays terminal for a failed crawl even with discovered URLs', () => {
    expect(
      resolveSiteHealthPhase(
        { ...base, status: 'failed', discovery_status: 'failed', analysis_status: 'cancelled' },
        'starter',
      ),
    ).toBe('terminal');
  });

  it('routes a cancelled crawl WITH score data to the dashboard (keeps partial scores)', () => {
    // Product rule: cancellation with partial data keeps the latest dashboard,
    // partial scores, and inventory visible — never a bare terminal card.
    const summary = {
      overall_score: 71,
      technical_score: 80,
      aeo_score: 62,
      selected_count: 10,
      analyzed_count: 4,
      issue_count: 3,
      scoring_version: 's1',
    };
    expect(
      resolveSiteHealthPhase(
        {
          ...base,
          status: 'cancelled',
          discovery_status: 'cancelled',
          analysis_status: 'cancelled',
          score_summary: summary,
        },
        'starter',
      ),
    ).toBe('dashboard');
    // Free too — a cancelled-with-data crawl always keeps its results.
    expect(
      resolveSiteHealthPhase(
        {
          ...base,
          status: 'cancelled',
          discovery_status: 'cancelled',
          analysis_status: 'cancelled',
          score_summary: summary,
        },
        'free',
      ),
    ).toBe('dashboard');
  });

  it('routes a failed crawl WITH score data to the dashboard (partial results survive)', () => {
    const summary = {
      overall_score: 55,
      technical_score: 60,
      aeo_score: 50,
      selected_count: 8,
      analyzed_count: 3,
      issue_count: 2,
      scoring_version: 's1',
    };
    expect(
      resolveSiteHealthPhase(
        { ...base, status: 'failed', discovery_status: 'failed', score_summary: summary },
        'starter',
      ),
    ).toBe('dashboard');
  });

  it('is deterministic: score data outranks the discovering/analyzing sub-states', () => {
    // Even mid-discovery, a landed projection means the dashboard is the one
    // authoritative outcome — precedence is explicit, not order-dependent.
    const summary = {
      overall_score: null,
      technical_score: null,
      aeo_score: null,
      selected_count: 5,
      analyzed_count: 1,
      issue_count: 0,
      scoring_version: 's1',
    };
    expect(
      resolveSiteHealthPhase(
        { ...base, status: 'running', discovery_status: 'running', score_summary: summary },
        'starter',
      ),
    ).toBe('dashboard');
  });

  it('routes a Free discovered crawl (analysis pending) to analyzing, not selection', () => {
    expect(
      resolveSiteHealthPhase(
        { ...base, discovery_status: 'sample_completed', analysis_status: 'pending' },
        'free',
      ),
    ).toBe('analyzing');
  });
});

describe('canonical-screen view-model (primaryAction / inventoryMode)', () => {
  it('offers Start on empty and Re-crawl on terminal', () => {
    expect(primaryActionForPhase('empty', false)).toBe('start');
    expect(primaryActionForPhase('terminal', false)).toBe('recrawl');
  });

  it('offers Cancel only while the crawl is actually active', () => {
    expect(primaryActionForPhase('discovering', true)).toBe('cancel');
    expect(primaryActionForPhase('analyzing', true)).toBe('cancel');
    expect(primaryActionForPhase('selection', true)).toBe('cancel');
    // A stale non-active crawl in a progress phase gets no dangling Cancel.
    expect(primaryActionForPhase('discovering', false)).toBe('none');
    expect(primaryActionForPhase('analyzing', false)).toBe('none');
  });

  it('leaves an inactive selection to the section buttons and gives the dashboard Re-crawl', () => {
    // A cancelled crawl's selection flow is driven by Save / Start analysis.
    expect(primaryActionForPhase('selection', false)).toBe('none');
    expect(primaryActionForPhase('dashboard', false)).toBe('recrawl');
    // A live projection dashboard (analysis still running) can still cancel.
    expect(primaryActionForPhase('dashboard', true)).toBe('cancel');
  });

  it('maps each phase onto the single inventory-section mode', () => {
    expect(inventoryModeForPhase('discovering')).toBe('discovering');
    expect(inventoryModeForPhase('selection')).toBe('selectable');
    expect(inventoryModeForPhase('analyzing')).toBe('analyzing');
    expect(inventoryModeForPhase('dashboard')).toBe('scored');
    expect(inventoryModeForPhase('empty')).toBe('none');
    expect(inventoryModeForPhase('terminal')).toBe('none');
  });
});

describe('score-data helpers (cancelled-with-data product rule)', () => {  const summary = {
    overall_score: 71,
    technical_score: 80,
    aeo_score: 62,
    selected_count: 10,
    analyzed_count: 4,
    issue_count: 3,
    scoring_version: 's1',
  };

  it('hasScoreData reflects a present score_summary', () => {
    expect(hasScoreData({ score_summary: summary })).toBe(true);
    expect(hasScoreData({ score_summary: null })).toBe(false);
  });
});

describe('dashboardRunNotice', () => {
  it('returns null for a cleanly completed crawl (no notice)', () => {
    expect(dashboardRunNotice({ status: 'completed' })).toBeNull();
  });

  it('labels a cancelled dashboard explicitly with a Cancelled badge + info tone', () => {
    const notice = dashboardRunNotice({ status: 'cancelled' });
    expect(notice?.badge).toBe('cancelled');
    expect(notice?.tone).toBe('info');
    expect(notice?.message).toMatch(/cancelled/i);
    expect(notice?.message).toMatch(/re-crawl/i);
  });

  it('labels a partial dashboard with a Partial badge + warning tone', () => {
    const notice = dashboardRunNotice({ status: 'partially_completed' });
    expect(notice?.badge).toBe('partial');
    expect(notice?.tone).toBe('warning');
  });

  it('labels a failed-with-data dashboard with a Failed badge', () => {
    const notice = dashboardRunNotice({ status: 'failed' });
    expect(notice?.badge).toBe('failed');
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
    expect(pageStatusBadgeValue('error')).toBe('danger');
    expect(pageStatusBadgeValue('blocked')).toBe('danger');
    expect(pageStatusBadgeValue('pending')).toBe('info');
    expect(pageStatusBadgeValue('not_selected')).toBe('info');
  });

  it('classifies error/blocked rows explicitly (not zero scores)', () => {
    expect(isErrorRow('failed')).toBe(true);
    expect(isErrorRow('error')).toBe(true);
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
