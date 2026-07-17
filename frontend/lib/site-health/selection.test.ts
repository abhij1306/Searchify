import { describe, expect, it } from 'vitest';

import {
  allStaged,
  commitCtaLabel,
  committedFromResponse,
  initStagedSelection,
  isStaged,
  missingMonitored,
  quotaStatus,
  rebaseOnServer,
  resetStaged,
  selectionDelta,
  setManyStaged,
  setStaged,
  toReplacePayload,
  toggleStaged,
  type CommittedSelection,
} from './selection';
import type { MonitoredUrlsResponse } from '@/lib/api/types';

const HOME = 'home-id';
const A = 'a-id';
const B = 'b-id';
const C = 'c-id';

function committed(ids: string[], version = 1): CommittedSelection {
  return { siteUrlIds: new Set(ids), version };
}

function monitoredResponse(ids: string[], version = 1): MonitoredUrlsResponse {
  return {
    project_id: 'p',
    selection_version: version,
    monitored_urls: ids.map((id) => ({
      site_url_id: id,
      normalized_url: `https://example.com/${id}`,
      display_url: `https://example.com/${id}`,
      title: null,
      active: true,
      selection_source: 'user',
      selected_at: null,
      deselected_at: null,
    })),
    quota: { used: ids.length, limit: 50 },
  };
}

describe('committedFromResponse', () => {
  it('collects active monitored ids and the version', () => {
    const committed = committedFromResponse(monitoredResponse([A, B], 7));
    expect(committed.version).toBe(7);
    expect([...committed.siteUrlIds].sort()).toEqual([A, B]);
  });

  it('ignores inactive monitored rows', () => {
    const res = monitoredResponse([A], 2);
    res.monitored_urls.push({
      site_url_id: B,
      normalized_url: 'https://example.com/b',
      display_url: 'https://example.com/b',
      title: null,
      active: false,
      selection_source: 'user',
      selected_at: null,
      deselected_at: '2026-07-15T00:00:00Z',
    });
    expect([...committedFromResponse(res).siteUrlIds]).toEqual([A]);
  });
});

describe('initStagedSelection (homepage-only first-use rule)', () => {
  it('stages the homepage when this is the first-ever commit (version 0)', () => {
    const s = initStagedSelection(committed([], 0), HOME);
    expect(isStaged(s, HOME)).toBe(true);
  });

  it('does NOT stage the homepage when a committed set exists', () => {
    const s = initStagedSelection(committed([A]), HOME);
    expect(isStaged(s, HOME)).toBe(false);
    expect(isStaged(s, A)).toBe(true);
  });

  it('does NOT stage the homepage for an intentional empty set (version advanced past 0)', () => {
    // The user committed an empty set (version bumped to 3) — an empty
    // committed set is never, by itself, evidence of "never used".
    const s = initStagedSelection(committed([], 3), HOME);
    expect(isStaged(s, HOME)).toBe(false);
    expect(s.staged.size).toBe(0);
  });

  it('does not stage a homepage when none is provided', () => {
    const s = initStagedSelection(committed([], 0), null);
    expect(s.staged.size).toBe(0);
  });

  it('copies the committed set as the initial staged set', () => {
    const s = initStagedSelection(committed([A, B]));
    expect([...s.staged].sort()).toEqual([A, B]);
  });
});

describe('staged selection persists across cursor pages', () => {
  it('keeps staged rows from earlier pages when toggling a later-page row', () => {
    // Page 1: stage A.
    let s = initStagedSelection(committed([]));
    s = toggleStaged(s, A);
    // Page 2 (A no longer visible): stage B — A must remain staged.
    s = toggleStaged(s, B);
    expect(isStaged(s, A)).toBe(true);
    expect(isStaged(s, B)).toBe(true);
  });

  it('setStaged / setManyStaged do not clobber existing staged rows', () => {
    let s = initStagedSelection(committed([]));
    s = setStaged(s, A, true);
    s = setManyStaged(s, [B, C], true);
    expect([...s.staged].sort()).toEqual([A, B, C].sort());
    s = setManyStaged(s, [B], false);
    expect(isStaged(s, B)).toBe(false);
    expect(isStaged(s, A)).toBe(true);
  });
});

describe('selectionDelta (delta display)', () => {
  it('reports additions and removals vs committed', () => {
    let s = initStagedSelection(committed([A, B]));
    s = toggleStaged(s, C); // add C
    s = toggleStaged(s, A); // remove A
    const delta = selectionDelta(s);
    expect(delta.added).toEqual([C]);
    expect(delta.removed).toEqual([A]);
    expect(delta.dirty).toBe(true);
  });

  it('is not dirty when staged equals committed', () => {
    const s = initStagedSelection(committed([A, B]));
    expect(selectionDelta(s).dirty).toBe(false);
  });

  it('resetStaged discards edits back to committed', () => {
    let s = initStagedSelection(committed([A]));
    s = toggleStaged(s, B);
    s = resetStaged(s);
    expect(selectionDelta(s).dirty).toBe(false);
    expect([...s.staged]).toEqual([A]);
  });
});

describe('quotaStatus (entitlement limit, not hard-coded 50)', () => {
  it('uses the entitlement monitored_url_limit', () => {
    let s = initStagedSelection(committed([]));
    s = setManyStaged(s, [A, B, C], true);
    const q = quotaStatus(s, { monitored_url_limit: 2 });
    expect(q.limit).toBe(2);
    expect(q.staged).toBe(3);
    expect(q.overLimit).toBe(true);
    expect(q.remaining).toBe(-1);
  });

  it('is within limit when at the entitlement bound', () => {
    let s = initStagedSelection(committed([]));
    s = setManyStaged(s, [A, B], true);
    const q = quotaStatus(s, { monitored_url_limit: 5 });
    expect(q.overLimit).toBe(false);
    expect(q.remaining).toBe(3);
  });

  it('counts URLs used by other projects toward the workspace quota', () => {
    let s = initStagedSelection(committed([]));
    s = setStaged(s, A, true);
    const q = quotaStatus(s, { monitored_url_limit: 3 }, 3);
    expect(q.staged).toBe(4);
    expect(q.overLimit).toBe(true);
  });

  it('respects a limit other than 50 (proves no hard-coded value)', () => {
    let s = initStagedSelection(committed([]));
    s = setManyStaged(s, Array.from({ length: 60 }, (_, i) => `u${i}`), true);
    expect(quotaStatus(s, { monitored_url_limit: 100 }).overLimit).toBe(false);
  });
});

describe('toReplacePayload (client claims no authority)', () => {
  it('produces sorted ids + the committed version', () => {
    let s = initStagedSelection(committed([B], 9));
    s = toggleStaged(s, A);
    const payload = toReplacePayload(s);
    expect(payload.site_url_ids).toEqual([A, B].sort());
    expect(payload.expected_selection_version).toBe(9);
  });
});

describe('rebaseOnServer (stale-revision recovery)', () => {
  it('re-applies the user intent on top of the server set and bumps version', () => {
    // Committed v1 = {A}. User stages +C, -A.
    let s = initStagedSelection(committed([A], 1));
    s = toggleStaged(s, C);
    s = toggleStaged(s, A);
    // Server advanced to v5 = {A, B} (another actor added B).
    const server = committed([A, B], 5);
    const rebased = rebaseOnServer(s, server);
    // Intent preserved: +C, -A → {B, C}; new baseline v5.
    expect([...rebased.staged].sort()).toEqual([B, C].sort());
    expect(rebased.committed.version).toBe(5);
    // A resubmit now carries the fresh version.
    expect(toReplacePayload(rebased).expected_selection_version).toBe(5);
  });
});

describe('allStaged + commitCtaLabel + missingMonitored', () => {
  it('allStaged is true only when every id on the page is staged', () => {
    let s = initStagedSelection(committed([A]));
    expect(allStaged(s, [A, B])).toBe(false);
    s = toggleStaged(s, B);
    expect(allStaged(s, [A, B])).toBe(true);
    expect(allStaged(s, [])).toBe(false);
  });

  it('commitCtaLabel reflects staged count and the entitlement limit', () => {
    let s = initStagedSelection(committed([]));
    s = setManyStaged(s, [A, B], true);
    expect(commitCtaLabel(s, 50)).toBe('Analyze 2 of 50 pages');
    expect(commitCtaLabel(s, 10)).toBe('Analyze 2 of 10 pages');
  });

  it('missingMonitored finds active monitored ids absent from the inventory', () => {
    const res = monitoredResponse([A, B]);
    const missing = missingMonitored(res, new Set([A]));
    expect(missing.map((m) => m.site_url_id)).toEqual([B]);
  });
});
