/**
 * Site Health page-type vocabulary + presentation helpers (v2 P1) — PURE.
 *
 * The SINGLE shared mapping from the backend `page_type` classification
 * vocabulary to humanized labels — every badge, filter control, and the
 * dashboard per-type breakdown reads it from here (no duplicated maps).
 * No transport, no React.
 */
import { pageTypeSchema } from '@/lib/api/schemas';
import type { PageType, PageTypeScoreSummary } from '@/lib/api/types';
import { titleCaseStatus } from '@/lib/utils';

/**
 * Every page type in stable display order (filter control + breakdown
 * table). Derived from the API-contract zod enum (the same derivation as
 * `lib/prompts/forms.ts` `intentValues`) so the vocabulary has exactly one
 * frontend owner.
 */
export const PAGE_TYPES: readonly PageType[] = pageTypeSchema.options;

/** Humanized label per page type — the one shared mapping. */
export const PAGE_TYPE_LABELS: Record<PageType, string> = {
  homepage: 'Homepage',
  article: 'Article',
  product: 'Product',
  category: 'Category',
  pricing: 'Pricing',
  docs: 'Docs',
  faq: 'FAQ',
  about_contact: 'About / Contact',
  other: 'Other',
};

/**
 * Display label for a page type. An unknown value (a vocabulary the frontend
 * has not caught up with) falls back to title-casing instead of rendering
 * blank — the same defensive fallback `issueTitle` applies to blank titles.
 */
export function pageTypeLabel(pageType: string): string {
  return PAGE_TYPE_LABELS[pageType as PageType] ?? titleCaseStatus(pageType);
}

/** One display row of the dashboard per-page-type score breakdown. */
export type PageTypeScoreRow = PageTypeScoreSummary & { page_type: string };

/**
 * Order a `score_summary.by_page_type` map for display: the `PAGE_TYPES`
 * order first, then any unknown types alphabetically — stable and
 * deterministic, never dependent on the API's map insertion order.
 */
export function byPageTypeRows(
  byPageType: Record<string, PageTypeScoreSummary>,
): PageTypeScoreRow[] {
  const rank = new Map<string, number>(PAGE_TYPES.map((type, index) => [type, index]));
  return Object.entries(byPageType)
    .map(([page_type, scores]) => ({ page_type, ...scores }))
    .sort((a, b) => {
      const aRank = rank.get(a.page_type) ?? PAGE_TYPES.length;
      const bRank = rank.get(b.page_type) ?? PAGE_TYPES.length;
      return aRank === bRank ? a.page_type.localeCompare(b.page_type) : aRank - bRank;
    });
}
