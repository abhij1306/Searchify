/**
 * productsApi contract tests (agentic commerce): request paths, query
 * building, the export URL helper, and fail-loud strict validation. Transport
 * is stubbed at global fetch (mirrors client.test.ts).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const UUID = '11111111-1111-4111-8111-111111111111';
const UUID2 = '22222222-2222-4222-8222-222222222222';
const UUID3 = '33333333-3333-4333-8333-333333333333';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const product = {
  id: UUID,
  project_id: UUID2,
  sku: 'AC-VB500',
  name: 'Acme VoltBike 500',
  aliases: ['VoltBike'],
  variants: [{ name: 'Graphite / Standard', sku: 'AC-VB500-GR', price: 2499.0 }],
  price: 2499.0,
  currency: 'USD',
  url: 'https://acme.com/p/voltbike',
  attributes: { brand: 'Acme', category: 'E-Bikes' },
  origin: 'manual',
  completeness: { score: 0.75, present: 9, total: 12, missing: ['gtin', 'mpn', 'condition'] },
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
};

const visibility = {
  project_id: UUID2,
  audit_id: UUID3,
  audit_status: 'completed',
  product_analyzer_version: 'product-analysis-1',
  product_scoring_rule_version: 'product-scoring-v1',
  total_mentions: 4,
  total_analyses: 2,
  products: [
    {
      product_id: UUID,
      sku: 'AC-VB500',
      name: 'Acme VoltBike 500',
      mention_count: 2,
      sov_share: 0.5,
      avg_rank: 1.0,
      rank_distribution: { top_1: 2, top_2_3: 0, top_4_5: 0, rank_6_plus: 0, unranked: 0 },
      price_mention_count: 2,
      price_accuracy_rate: 1.0,
    },
  ],
  competitor_products: [
    {
      competitor_product_id: UUID3,
      competitor_name: 'Globex',
      name: 'Globex CityBike 450',
      mention_count: 2,
      sov_share: 0.5,
      avg_rank: 2.0,
      rank_distribution: { top_1: 0, top_2_3: 2, top_4_5: 0, rank_6_plus: 0, unranked: 0 },
      price_mention_count: 2,
      price_accuracy_rate: null,
    },
  ],
  created_at: '2026-07-15T00:00:00Z',
};

const evidence = {
  items: [
    {
      mention_id: UUID,
      audit_id: UUID3,
      task_id: UUID2,
      artifact_id: UUID,
      logical_engine: 'gemini',
      transport_model: 'gemini-2.5-pro',
      prompt_text: 'best option 0',
      prompt_index: 0,
      repetition: 0,
      matched_name: 'Acme VoltBike 500',
      matched_sku: 'AC-VB500',
      first_offset: 4,
      rank_position: 1,
      price_text: '$2,499.00',
      price_value: 2499.0,
      price_currency: 'USD',
      price_matches_catalog: true,
      created_at: '2026-07-15T00:00:00Z',
    },
  ],
  truncated: false,
};

describe('productsApi', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('lists the catalog at the project-scoped path', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([product]));
    vi.stubGlobal('fetch', fetchMock);

    const { productsApi } = await import('./products');
    const rows = await productsApi.list(UUID2);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`/api/v1/projects/${UUID2}/products`);
    expect(rows).toHaveLength(1);
    expect(rows[0]?.completeness.missing).toContain('gtin');
  });

  it('creates / updates / removes a product on the flat paths', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(product, 201))
      .mockResolvedValueOnce(jsonResponse(product))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    const { productsApi } = await import('./products');
    const input = { sku: 'AC-VB500', name: 'Acme VoltBike 500', currency: 'usd' };
    await productsApi.create(UUID2, input);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`/api/v1/projects/${UUID2}/products`);
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe('POST');
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(input);

    await productsApi.update(UUID, { price: 2399.0 });
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(`/api/v1/products/${UUID}`);
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe('PATCH');

    await productsApi.remove(UUID);
    expect(String(fetchMock.mock.calls[2]?.[0])).toBe(`/api/v1/products/${UUID}`);
    expect(fetchMock.mock.calls[2]?.[1]?.method).toBe('DELETE');
  });

  it('imports CSV as FormData and rows as a { products } JSON body', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse([product])));
    vi.stubGlobal('fetch', fetchMock);

    const { productsApi } = await import('./products');
    await productsApi.importCsv(UUID2, new File(['sku,name\nAC-VB500,Acme'], 'products.csv'));
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`/api/v1/projects/${UUID2}/products/import`);
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBeInstanceOf(FormData);

    const rows = [{ sku: 'AC-VB500', name: 'Acme VoltBike 500' }];
    await productsApi.importRows(UUID2, rows);
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(`/api/v1/projects/${UUID2}/products/import`);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({ products: rows });
  });

  it('scopes competitor-product CRUD to the project / flat paths', async () => {
    const competitorProduct = {
      id: UUID3,
      project_id: UUID2,
      competitor_id: UUID,
      name: 'Globex CityBike 450',
      aliases: [],
      price: 2399.0,
      currency: 'USD',
      url: '',
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:00:00Z',
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([competitorProduct]))
      .mockResolvedValueOnce(jsonResponse(competitorProduct, 201))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    const { productsApi } = await import('./products');
    const listed = await productsApi.listCompetitorProducts(UUID2);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `/api/v1/projects/${UUID2}/competitor-products`,
    );
    expect(listed[0]?.competitor_id).toBe(UUID);

    await productsApi.createCompetitorProduct(UUID2, {
      competitor_id: UUID,
      name: 'Globex CityBike 450',
    });
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      `/api/v1/projects/${UUID2}/competitor-products`,
    );

    await productsApi.removeCompetitorProduct(UUID3);
    expect(String(fetchMock.mock.calls[2]?.[0])).toBe(`/api/v1/competitor-products/${UUID3}`);
  });

  it('builds the visibility path with an optional audit_id query', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(visibility)));
    vi.stubGlobal('fetch', fetchMock);

    const { productsApi } = await import('./products');
    const latest = await productsApi.getProductVisibility(UUID2);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `/api/v1/projects/${UUID2}/products/visibility`,
    );
    expect(latest.audit_status).toBe('completed');
    expect(latest.products[0]?.sov_share).toBe(0.5);

    await productsApi.getProductVisibility(UUID2, { audit_id: UUID3 });
    expect(String(fetchMock.mock.calls[1]?.[0])).toBe(
      `/api/v1/projects/${UUID2}/products/visibility?audit_id=${UUID3}`,
    );
  });

  it('builds the evidence path with audit/engine/limit filters', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(evidence)));
    vi.stubGlobal('fetch', fetchMock);

    const { productsApi } = await import('./products');
    const unfiltered = await productsApi.getProductEvidence(UUID);
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      `/api/v1/products/${UUID}/visibility/evidence`,
    );
    expect(unfiltered.truncated).toBe(false);
    expect(unfiltered.items[0]?.price_matches_catalog).toBe(true);

    await productsApi.getProductEvidence(UUID, {
      audit_id: UUID3,
      engine: 'gemini',
      limit: 50,
    });
    const url = String(fetchMock.mock.calls[1]?.[0]);
    expect(url.startsWith(`/api/v1/products/${UUID}/visibility/evidence?`)).toBe(true);
    const params = new URLSearchParams(url.split('?')[1]);
    expect(params.get('audit_id')).toBe(UUID3);
    expect(params.get('engine')).toBe('gemini');
    expect(params.get('limit')).toBe('50');
  });

  it('builds same-origin export URLs with an optional audit_id', async () => {
    const { productsApi } = await import('./products');
    expect(productsApi.exportCsvUrl(UUID2)).toBe(
      `/api/v1/projects/${UUID2}/products/visibility/export.csv`,
    );
    expect(productsApi.exportCsvUrl(UUID2, UUID3)).toBe(
      `/api/v1/projects/${UUID2}/products/visibility/export.csv?audit_id=${UUID3}`,
    );
  });

  it('fails loud on contract drift (numeric id, missing completeness)', async () => {
    const drifted = { ...product, id: 7 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([drifted]));
    vi.stubGlobal('fetch', fetchMock);

    const { productsApi } = await import('./products');
    await expect(productsApi.list(UUID2)).rejects.toThrow(
      /API validation failure in products\.list/,
    );

    const incomplete = { ...product };
    delete (incomplete as Record<string, unknown>).completeness;
    const { strictValidate, productSchema } = await import('./schemas');
    expect(() => strictValidate(productSchema, incomplete, 't')).toThrow(
      /API validation failure in t/,
    );
  });
});
