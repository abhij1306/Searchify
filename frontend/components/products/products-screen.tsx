'use client';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useProjectContext } from '@/lib/project/project-context';
import { useProductsTab, useProductVisibilityQueries } from '@/lib/products/use-products-screen';

import { CatalogPanel } from './catalog-panel';
import { ProductVisibilityPanel } from './product-visibility-panel';
import { ProductsTabs } from './products-tabs';

export function ProductsScreenSkeleton() {
  return (
    <div className="grid gap-4" aria-hidden>
      <Skeleton className="h-9 w-72" />
      <Card>
        <CardContent className="grid gap-3">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-48 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

/**
 * Products workspace container (agentic commerce). Resolves the active
 * project (F5 context) and renders one shell: an accessible two-tab tablist
 * (**Catalog** default | **Visibility**) with exactly one panel at a time;
 * the active tab is mirrored in `?tab=` (mirror `visibility-dashboard.tsx`).
 */
export function ProductsScreen() {
  const { activeProject, isLoading: isProjectLoading } = useProjectContext();
  const projectId = activeProject?.id ?? null;

  const { activeTab, selectTab } = useProductsTab();
  const visibilityQueries = useProductVisibilityQueries(projectId, activeTab === 'visibility');

  if (isProjectLoading) {
    return <ProductsScreenSkeleton />;
  }

  if (!projectId) {
    return <Alert tone="info">Select or create a project to manage its product catalog.</Alert>;
  }

  const panel =
    activeTab === 'visibility' ? (
      <ProductVisibilityPanel
        projectId={projectId}
        queries={visibilityQueries}
        onGoToCatalog={() => selectTab('catalog')}
      />
    ) : (
      <CatalogPanel projectId={projectId} />
    );

  return <ProductsTabs activeTab={activeTab} onSelectTab={selectTab} panel={panel} />;
}
