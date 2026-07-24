import { describe, expect, it } from 'vitest';

import type { PageTypeScoreSummary } from '@/lib/api/types';
import { PAGE_TYPES, byPageTypeRows, pageTypeLabel } from './page-types';

describe('pageTypeLabel (the single shared mapping)', () => {
  it('has a humanized label for every page type in the vocabulary', () => {
    for (const pageType of PAGE_TYPES) {
      expect(pageTypeLabel(pageType)).not.toBe(pageType);
      expect(pageTypeLabel(pageType).length).toBeGreaterThan(0);
    }
  });

  it('maps the multi-word and acronym types exactly', () => {
    expect(pageTypeLabel('about_contact')).toBe('About / Contact');
    expect(pageTypeLabel('faq')).toBe('FAQ');
    expect(pageTypeLabel('homepage')).toBe('Homepage');
    expect(pageTypeLabel('other')).toBe('Other');
  });

  it('falls back to title-casing an unknown type instead of rendering blank', () => {
    expect(pageTypeLabel('landing_page')).toBe('Landing Page');
  });
});

describe('byPageTypeRows (dashboard breakdown ordering)', () => {
  const bucket = (analyzed_count: number): PageTypeScoreSummary => ({
    analyzed_count,
    technical_score: 80,
    aeo_score: 62,
    overall_score: 71,
  });

  it('returns [] for an empty breakdown', () => {
    expect(byPageTypeRows({})).toEqual([]);
  });

  it('orders rows by the PAGE_TYPES display order, not insertion order', () => {
    const rows = byPageTypeRows({
      pricing: bucket(1),
      homepage: bucket(2),
      article: bucket(3),
    });
    expect(rows.map((row) => row.page_type)).toEqual(['homepage', 'article', 'pricing']);
  });

  it('spreads the analyzed count + mean scores onto each row', () => {
    const [row] = byPageTypeRows({ docs: bucket(7) });
    expect(row).toEqual({
      page_type: 'docs',
      analyzed_count: 7,
      technical_score: 80,
      aeo_score: 62,
      overall_score: 71,
    });
  });

  it('appends unknown types alphabetically after the known vocabulary', () => {
    const rows = byPageTypeRows({
      zebra_page: bucket(1),
      article: bucket(2),
      landing_page: bucket(3),
    });
    expect(rows.map((row) => row.page_type)).toEqual(['article', 'landing_page', 'zebra_page']);
  });
});
