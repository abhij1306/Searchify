/**
 * Products (agentic commerce) display helpers — pure, framework-free.
 *
 * The tab model for the `/products` workspace (Catalog | Visibility) plus the
 * formatters the catalog table, the visibility summary strip, and the
 * rankings tables share. Every number rendered here is derived from persisted
 * backend values — never invented.
 */
import type {
  CompetitorProductVisibilityEntry,
  LogicalEngine,
  ProductVisibility,
  ProductVisibilityEntry,
} from '@/lib/api/types';

/** The two `/products` workspace tabs, in display order; Catalog is default. */
export type ProductsTab = 'catalog' | 'visibility';

/** Engine filter value for the products surfaces (`all` = cross-engine). */
export type ProductEngineFilter = LogicalEngine | 'all';

export const PRODUCTS_TABS: readonly { id: ProductsTab; label: string }[] = [
  { id: 'catalog', label: 'Catalog' },
  { id: 'visibility', label: 'Visibility' },
] as const;

const DEFAULT_TAB: ProductsTab = 'catalog';

/** Narrow an arbitrary `?tab=` value to a known tab, else the default. */
export function normalizeProductsTab(value: string | null | undefined): ProductsTab {
  return PRODUCTS_TABS.some((tab) => tab.id === value) ? (value as ProductsTab) : DEFAULT_TAB;
}

/** ISO-4217 → common symbol (display only; the code stays the source). */
const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  AUD: 'A$',
  CAD: 'C$',
};

/** `$2,499.00` / `€2,499.00` / `2,499.00 CHF` / `—` when no price. */
export function formatPrice(price: number | null | undefined, currency: string): string {
  if (price === null || price === undefined) return '—';
  const amount = price.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const code = (currency ?? '').trim().toUpperCase();
  const symbol = code ? CURRENCY_SYMBOLS[code] : undefined;
  if (symbol) return `${symbol}${amount}`;
  return code ? `${amount} ${code}` : amount;
}

/** `0.482` → `48%`; null/undefined → `—`. */
export function formatPercent(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return '—';
  return `${Math.round(rate * 100)}%`;
}

/** `1.6`; `—` when the product was never rank-listed. */
export function formatAvgRank(avgRank: number | null | undefined): string {
  if (avgRank === null || avgRank === undefined) return '—';
  return avgRank.toFixed(1);
}

/** Rank-bucket display order + labels (backend `PRODUCT_RANK_BUCKETS`). */
export const RANK_BUCKET_ORDER = [
  'top_1',
  'top_2_3',
  'top_4_5',
  'rank_6_plus',
  'unranked',
] as const;

export const RANK_BUCKET_LABELS: Record<(typeof RANK_BUCKET_ORDER)[number], string> = {
  top_1: 'Top 1',
  top_2_3: 'Top 2–3',
  top_4_5: 'Top 4–5',
  rank_6_plus: '6+',
  unranked: 'Unranked',
};

/** Mentions that landed in a rank list (total minus the unranked bucket). */
function rankedMentionCount(
  entry: ProductVisibilityEntry | CompetitorProductVisibilityEntry,
): number {
  const distribution = entry.rank_distribution ?? {};
  const unranked = distribution.unranked ?? 0;
  return Math.max(entry.mention_count - unranked, 0);
}

/** The catalog-wide summary strip above the visibility rankings. */
export type ProductVisibilitySummary = {
  /** Own-product mentions in the selected run. */
  ownMentions: number;
  /** All product + competitor-product mentions in the selected run. */
  totalMentions: number;
  /** Own share of all product mentions (0–1); null when the run has none. */
  sov: number | null;
  /**
   * Rank-weighted mean over ranked mentions (per-entry `avg_rank` re-weighted
   * by that entry's ranked-mention count); null when nothing was rank-listed.
   */
  avgRank: number | null;
  /**
   * Price-mention accuracy across own products: per-product persisted rates
   * weighted by price-mention volume; null when no price mention was
   * verifiable against the catalog.
   */
  priceAccuracy: number | null;
};

/** Derive the summary strip from the persisted visibility projection. */
export function summarizeProductVisibility(
  visibility: ProductVisibility,
): ProductVisibilitySummary {
  const ownMentions = visibility.products.reduce(
    (sum, entry) => sum + entry.mention_count,
    0,
  );
  const totalMentions = visibility.total_mentions;

  let rankSum = 0;
  let rankCount = 0;
  let accuracySum = 0;
  let accuracyWeight = 0;
  for (const entry of visibility.products) {
    const ranked = rankedMentionCount(entry);
    if (entry.avg_rank !== null && ranked > 0) {
      rankSum += entry.avg_rank * ranked;
      rankCount += ranked;
    }
    if (entry.price_accuracy_rate !== null && entry.price_mention_count > 0) {
      accuracySum += entry.price_accuracy_rate * entry.price_mention_count;
      accuracyWeight += entry.price_mention_count;
    }
  }

  return {
    ownMentions,
    totalMentions,
    sov: totalMentions > 0 ? ownMentions / totalMentions : null,
    avgRank: rankCount > 0 ? rankSum / rankCount : null,
    priceAccuracy: accuracyWeight > 0 ? accuracySum / accuracyWeight : null,
  };
}
