import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ApiError } from '@/lib/api/errors';
import { queryKeys } from '@/lib/api/query-keys';
import type { ProductVisibility } from '@/lib/api/types';
import type { useProductVisibilityQueries } from '@/lib/products/use-products-screen';

import { ProductVisibilityPanel } from './product-visibility-panel';

type VisibilityQueries = ReturnType<typeof useProductVisibilityQueries>;

const PROJECT = '11111111-1111-4111-8111-111111111111';
const PRODUCT = '22222222-2222-4222-8222-222222222222';
const COMPETITOR_PRODUCT = '33333333-3333-4333-8333-333333333333';

function makeQueries(overrides: Record<string, unknown> = {}): VisibilityQueries {
  return {
    auditsQuery: { isLoading: false },
    runOptions: [],
    activeRunId: null,
    selectRun: vi.fn(),
    engine: 'all',
    setEngine: vi.fn(),
    engineParam: undefined,
    visibilityQuery: { isLoading: false, isError: false, data: undefined },
    ...overrides,
  } as unknown as VisibilityQueries;
}

function makeVisibility(): ProductVisibility {
  return {
    project_id: PROJECT,
    audit_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    audit_status: 'completed',
    product_analyzer_version: 'product-analysis-1',
    product_scoring_rule_version: 'product-scoring-v1',
    total_mentions: 4,
    total_analyses: 2,
    products: [
      {
        product_id: PRODUCT,
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
        competitor_product_id: COMPETITOR_PRODUCT,
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
}

describe('ProductVisibilityPanel states', () => {
  it('renders a loading skeleton while the projection loads', () => {
    const { container } = render(
      <ProductVisibilityPanel
        projectId={PROJECT}
        queries={makeQueries({ visibilityQuery: { isLoading: true, isError: false } })}
        onGoToCatalog={() => {}}
      />,
    );
    expect(screen.queryByText('Product rankings')).not.toBeInTheDocument();
    expect(container.querySelectorAll('[aria-hidden]').length).toBeGreaterThan(0);
  });

  it('renders the no-audit empty state with a catalog CTA on a 404', () => {
    const onGoToCatalog = vi.fn();
    render(
      <ProductVisibilityPanel
        projectId={PROJECT}
        queries={makeQueries({
          visibilityQuery: {
            isLoading: false,
            isError: true,
            error: new ApiError('not found', 404, '', 'req-1'),
          },
        })}
        onGoToCatalog={onGoToCatalog}
      />,
    );
    expect(screen.getByText('No product visibility yet')).toBeInTheDocument();
    screen.getByRole('button', { name: /Go to Catalog/ }).click();
    expect(onGoToCatalog).toHaveBeenCalled();
  });

  it('keeps the run selector reachable on a 404 for an explicitly selected run', () => {
    // Regression: picking a run without product metrics used to swap the whole
    // panel for the empty state — the selection stuck (screen-level state) and
    // the only way back to "Latest" was a full page reload.
    render(
      <ProductVisibilityPanel
        projectId={PROJECT}
        queries={makeQueries({
          activeRunId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
          runOptions: [{ id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', label: 'Jul 24, 2026' }],
          visibilityQuery: {
            isLoading: false,
            isError: true,
            error: new ApiError('not found', 404, '', 'req-1'),
          },
        })}
        onGoToCatalog={() => {}}
      />,
    );
    expect(screen.getByText('No product visibility yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Select run' })).toBeInTheDocument();
    expect(screen.getByText(/without product metrics/)).toBeInTheDocument();
  });

  it('hides the run selector on a 404 with no explicit run selection', () => {
    render(
      <ProductVisibilityPanel
        projectId={PROJECT}
        queries={makeQueries({
          visibilityQuery: {
            isLoading: false,
            isError: true,
            error: new ApiError('not found', 404, '', 'req-1'),
          },
        })}
        onGoToCatalog={() => {}}
      />,
    );
    expect(screen.getByText('No product visibility yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Select run' })).not.toBeInTheDocument();
  });

  it('renders the summary strip and both rankings tables with data', () => {
    render(
      <ProductVisibilityPanel
        projectId={PROJECT}
        queries={makeQueries({
          visibilityQuery: { isLoading: false, isError: false, data: makeVisibility() },
        })}
        onGoToCatalog={() => {}}
      />,
    );

    // Summary strip (computed from the persisted projection).
    expect(screen.getByText('Product SOV')).toBeInTheDocument();
    // 50% appears both in the SOV card and the table SOV column.
    expect(screen.getAllByText('50%').length).toBeGreaterThan(0);
    expect(screen.getByText('Product mentions')).toBeInTheDocument();
    expect(screen.getByText('Avg rank in product lists')).toBeInTheDocument();
    expect(screen.getByText('Price-mention accuracy')).toBeInTheDocument();

    // Own + competitor sections.
    expect(screen.getByText('Product rankings')).toBeInTheDocument();
    expect(screen.getByText('Competitor products')).toBeInTheDocument();
    expect(screen.getByText('Acme VoltBike 500')).toBeInTheDocument();
    expect(screen.getByText('Globex CityBike 450')).toBeInTheDocument();
    expect(screen.getByText('You')).toBeInTheDocument();

    // The own product links to its evidence drill-down.
    expect(screen.getByRole('link', { name: 'Acme VoltBike 500' })).toHaveAttribute(
      'href',
      `/products/${PRODUCT}`,
    );
    // The rank-distribution bar exposes bucket counts non-visually.
    expect(screen.getByRole('img', { name: /Top 1: 2, Top 2–3: 0/ })).toBeInTheDocument();
  });

  it('defaults the run selector to Latest and builds the export URL for it', () => {
    render(
      <ProductVisibilityPanel
        projectId={PROJECT}
        queries={makeQueries({
          visibilityQuery: { isLoading: false, isError: false, data: makeVisibility() },
        })}
        onGoToCatalog={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'Select run' })).toHaveTextContent('Latest');
    expect(screen.getByRole('link', { name: /Export CSV/ })).toHaveAttribute(
      'href',
      `/api/v1/projects/${PROJECT}/products/visibility/export.csv`,
    );
  });
});

describe('product visibility query key', () => {
  it('defaults to the latest audit (and all engines) in the cache key', () => {
    expect(queryKeys.products.visibility(PROJECT)).toEqual([
      'products',
      'visibility',
      PROJECT,
      'latest',
      'all',
    ]);
    expect(queryKeys.products.visibility(PROJECT, 'abc', 'gemini')).toEqual([
      'products',
      'visibility',
      PROJECT,
      'abc',
      'gemini',
    ]);
  });
});
