'use client';

import { Suspense } from 'react';

import { TooltipProvider } from '@/components/ui/tooltip';
import { ProductsScreen, ProductsScreenSkeleton } from '@/components/products/products-screen';

/**
 * Products workspace (agentic commerce): one shell with two tabs —
 *   - **Catalog** (default): the project's own product catalog (CRUD + CSV
 *     import) with per-SKU completeness badges;
 *   - **Visibility**: the selected run's product-vs-competitor projection —
 *     share of voice, mentions, rank distribution, and price accuracy.
 * The active tab is mirrored in `?tab=`. The page title renders in the top
 * bar (F5), so there is no in-page header block.
 */
export default function ProductsPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <Suspense fallback={<ProductsScreenSkeleton />}>
          <ProductsScreen />
        </Suspense>
      </div>
    </TooltipProvider>
  );
}
