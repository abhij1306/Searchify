import { describe, expect, it } from 'vitest';

import type { ProductVisibility } from '@/lib/api/types';

import {
  formatAvgRank,
  formatPercent,
  formatPrice,
  normalizeProductsTab,
  summarizeProductVisibility,
} from './catalog';
import { parseProductCsv, validProductRows } from './csv';
import { emptyProductForm, formValuesToProductUpdate } from './forms';

describe('normalizeProductsTab', () => {
  it('defaults to catalog and passes through known tabs', () => {
    expect(normalizeProductsTab(null)).toBe('catalog');
    expect(normalizeProductsTab('bogus')).toBe('catalog');
    expect(normalizeProductsTab('visibility')).toBe('visibility');
    expect(normalizeProductsTab('catalog')).toBe('catalog');
  });
});

describe('formatters', () => {
  it('formats prices with currency symbols and placeholders', () => {
    expect(formatPrice(2499, 'USD')).toBe('$2,499.00');
    expect(formatPrice(2499.5, 'eur')).toBe('€2,499.50');
    expect(formatPrice(100, 'CHF')).toBe('100.00 CHF');
    expect(formatPrice(null, 'USD')).toBe('—');
  });

  it('formats percents and ranks with null placeholders', () => {
    expect(formatPercent(0.482)).toBe('48%');
    expect(formatPercent(null)).toBe('—');
    expect(formatAvgRank(1.6)).toBe('1.6');
    expect(formatAvgRank(null)).toBe('—');
  });
});

describe('summarizeProductVisibility', () => {
  const base: ProductVisibility = {
    project_id: '11111111-1111-4111-8111-111111111111',
    audit_id: '22222222-2222-4222-8222-222222222222',
    audit_status: 'completed',
    product_analyzer_version: 'a1',
    product_scoring_rule_version: 'r1',
    total_mentions: 10,
    total_analyses: 4,
    products: [
      {
        product_id: '33333333-3333-4333-8333-333333333333',
        sku: 'A',
        name: 'Product A',
        mention_count: 4,
        sov_share: 0.4,
        avg_rank: 1.5,
        rank_distribution: { top_1: 2, top_2_3: 2, top_4_5: 0, rank_6_plus: 0, unranked: 0 },
        price_mention_count: 3,
        price_accuracy_rate: 1.0,
      },
      {
        product_id: '44444444-4444-4444-8444-444444444444',
        sku: 'B',
        name: 'Product B',
        mention_count: 2,
        sov_share: 0.2,
        avg_rank: 4.0,
        // One mention unranked: only one ranked mention feeds the mean.
        rank_distribution: { top_1: 0, top_2_3: 0, top_4_5: 1, rank_6_plus: 0, unranked: 1 },
        price_mention_count: 1,
        price_accuracy_rate: 0.0,
      },
    ],
    competitor_products: [],
    created_at: '2026-07-15T00:00:00Z',
  };

  it('computes SOV, rank-weighted avg rank, and price accuracy', () => {
    const summary = summarizeProductVisibility(base);
    expect(summary.ownMentions).toBe(6);
    expect(summary.totalMentions).toBe(10);
    expect(summary.sov).toBeCloseTo(0.6);
    // (1.5*4 + 4.0*1) / 5 ranked mentions = 2.0
    expect(summary.avgRank).toBeCloseTo(2.0);
    // (1.0*3 + 0.0*1) / 4 price mentions = 0.75
    expect(summary.priceAccuracy).toBeCloseTo(0.75);
  });

  it('returns nulls when nothing was mentioned', () => {
    const summary = summarizeProductVisibility({
      ...base,
      total_mentions: 0,
      products: [],
    });
    expect(summary.sov).toBeNull();
    expect(summary.avgRank).toBeNull();
    expect(summary.priceAccuracy).toBeNull();
  });
});

describe('parseProductCsv', () => {
  it('parses header + rows with attributes and a variant', () => {
    const parsed = parseProductCsv(
      'name,sku,variant,category,price,currency,url,gtin\n' +
        'VoltCity Commuter 500,VC-EB500-GR,Graphite / Standard,E-Bikes,"$2,499.00",usd,https://x.example/p,0123\n',
    );
    expect(parsed.errors).toEqual([]);
    expect(parsed.rows).toHaveLength(1);
    const row = parsed.rows[0]!;
    expect(row.errors).toEqual([]);
    expect(row.input).toEqual({
      sku: 'VC-EB500-GR',
      name: 'VoltCity Commuter 500',
      aliases: [],
      variants: [{ name: 'Graphite / Standard' }],
      price: 2499.0,
      currency: 'USD',
      url: 'https://x.example/p',
      attributes: { category: 'E-Bikes', gtin: '0123' },
    });
    expect(validProductRows(parsed)).toHaveLength(1);
  });

  it('rejects headerless files (matching the backend)', () => {
    const parsed = parseProductCsv('VoltCity Commuter 500,2499.00\n');
    expect(parsed.rows).toEqual([]);
    expect(parsed.errors[0]).toMatch(/header row is required/i);
  });

  it('flags rows without a sku as not importable and clears bad prices', () => {
    const parsed = parseProductCsv(
      'name,sku,price\nNoSku,,10\nBadPrice,SKU-2,not-a-price\n',
    );
    expect(parsed.rows[0]!.errors[0]).toMatch(/SKU is required/);
    expect(parsed.rows[1]!.warnings[0]).toMatch(/Unparseable price/);
    expect(parsed.rows[1]!.input.price).toBeNull();
    // Only the row with a sku is importable.
    expect(validProductRows(parsed).map((row) => row.sku)).toEqual(['SKU-2']);
  });

  it('falls back to the sku as name and splits aliases', () => {
    const parsed = parseProductCsv('sku,aliases\nSKU-1,Volt 500 | VC500\n');
    expect(parsed.rows[0]!.input.name).toBe('SKU-1');
    expect(parsed.rows[0]!.input.aliases).toEqual(['Volt 500', 'VC500']);
  });

  it('folds spaced headers to underscores like the backend import', () => {
    // `Product SKU` / `Currency Code` are accepted by the server
    // (csv_import folds spaces to underscores); the browser preview must too.
    const parsed = parseProductCsv('Product SKU,Product Name,Price Amount,Currency Code\nSKU-9,Volt,10,usd\n');
    expect(parsed.errors).toEqual([]);
    expect(parsed.rows[0]!.input).toMatchObject({
      sku: 'SKU-9',
      name: 'Volt',
      price: 10,
      currency: 'USD',
    });
  });

  it('strips letter-prefixed currency symbols from prices (backend parity)', () => {
    for (const raw of ['US$100', 'A$100', 'C$100', 'AU$100', 'CA$100', '€100', '£100']) {
      const parsed = parseProductCsv(`sku,price\nSKU-1,"${raw}"\n`);
      expect(parsed.rows[0]!.input.price).toBe(100);
    }
  });
});

describe('formValuesToProductUpdate', () => {
  const existing = {
    id: 'p1',
    project_id: 'proj',
    sku: 'SKU-1',
    name: 'Volt',
    aliases: ['V'],
    variants: [
      { name: 'Graphite', sku: 'SKU-1-G', price: 2499 },
      { name: 'Silver', sku: 'SKU-1-S', price: 2599 },
    ],
    price: 2499,
    currency: 'USD',
    url: 'https://x.example/p',
    attributes: { brand: 'Acme', mpn: 'MPN-1', condition: 'new' },
    origin: 'imported',
    completeness: { score: 1, present: 12, total: 12, missing: [] },
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  } as const;

  it('preserves form-unmanaged attribute keys and variants on edit', () => {
    const update = formValuesToProductUpdate(existing as never, {
      ...emptyProductForm,
      name: 'Volt Renamed',
      sku: 'SKU-1',
      brand: 'Acme 2',
    });
    // Form-owned keys overwritten; unmanaged keys (mpn/condition) survive.
    expect(update.attributes).toEqual({ brand: 'Acme 2', mpn: 'MPN-1', condition: 'new' });
    // variants[0].name overwritten in place; every other variant preserved.
    expect(update.variants).toEqual([
      { name: 'Graphite', sku: 'SKU-1-G', price: 2499 },
      { name: 'Silver', sku: 'SKU-1-S', price: 2599 },
    ]);
    expect(update.name).toBe('Volt Renamed');
  });

  it('writes the single form variant for a product without variants', () => {
    const update = formValuesToProductUpdate(
      { ...existing, variants: [] } as never,
      { ...emptyProductForm, sku: 'SKU-1', variant: 'Graphite' },
    );
    expect(update.variants).toEqual([{ name: 'Graphite' }]);
  });
});
