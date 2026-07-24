import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { TooltipProvider } from '@/components/ui/tooltip';
import type { Product } from '@/lib/api/types';

import { CatalogTable } from './catalog-table';

function makeProduct(n: number, overrides: Partial<Product> = {}): Product {
  return {
    id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
    project_id: '11111111-1111-4111-8111-111111111111',
    sku: `SKU-${n}`,
    name: `Product number ${n}`,
    aliases: [],
    variants: [{ name: 'Graphite / Standard', sku: `SKU-${n}-GR`, price: 2499.0 }],
    price: 2499.0,
    currency: 'USD',
    url: 'https://example.com/p',
    attributes: { brand: 'Acme' },
    origin: 'manual',
    completeness: { score: 1, present: 12, total: 12, missing: [] },
    created_at: '2026-07-15T00:00:00Z',
    updated_at: '2026-07-15T00:00:00Z',
    ...overrides,
  } as Product;
}

function renderTable(products: Product[]) {
  return render(
    <TooltipProvider>
      <CatalogTable products={products} onEdit={() => {}} onDelete={() => {}} />
    </TooltipProvider>,
  );
}

describe('CatalogTable completeness badge', () => {
  it('renders the complete badge as present/total · Complete', () => {
    renderTable([makeProduct(1)]);
    expect(screen.getByText('12/12 · Complete')).toBeInTheDocument();
  });

  it('renders the missing count on the badge when attributes are missing', () => {
    renderTable([
      makeProduct(1, {
        completeness: { score: 0.75, present: 9, total: 12, missing: ['gtin', 'mpn', 'condition'] },
      }),
    ]);

    // The badge carries the count (never color-only); the missing list is in
    // its tooltip (radix tooltips don't open under jsdom hover).
    expect(screen.getByText('9/12 · 3 missing')).toBeInTheDocument();
  });
});

describe('CatalogTable rows', () => {
  it('links the product name to the evidence drill-down and shows the variant', () => {
    const product = makeProduct(1);
    renderTable([product]);

    const link = screen.getByRole('link', { name: 'Product number 1' });
    expect(link).toHaveAttribute('href', `/products/${product.id}`);
    expect(screen.getByText('Graphite / Standard')).toBeInTheDocument();
    expect(screen.getByText('SKU-1')).toBeInTheDocument();
    expect(screen.getByText('$2,499.00')).toBeInTheDocument();
    expect(screen.getByText('manual')).toBeInTheDocument();
  });

  it('renders placeholders for missing price and variants', () => {
    renderTable([makeProduct(1, { price: null, variants: [] })]);
    const row = screen.getByRole('row', { name: /Product number 1/ });
    expect(within(row).getAllByText('—')).toHaveLength(2);
  });

  it('fires edit and delete callbacks from the row actions', async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    const product = makeProduct(1);
    render(
      <TooltipProvider>
        <CatalogTable products={[product]} onEdit={onEdit} onDelete={onDelete} />
      </TooltipProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'Actions for Product number 1' }));
    await user.click(screen.getByRole('menuitem', { name: /Edit/ }));
    expect(onEdit).toHaveBeenCalledWith(product);

    await user.click(screen.getByRole('button', { name: 'Actions for Product number 1' }));
    await user.click(screen.getByRole('menuitem', { name: /Delete/ }));
    expect(onDelete).toHaveBeenCalledWith(product);
  });
});
