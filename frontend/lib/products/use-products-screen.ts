'use client';

/**
 * State + queries for the `/products` workspace.
 *
 * `useProductsTab` mirrors the active tab into `?tab=` (Catalog is default)
 * so refresh / back / forward preserve it. `useCatalogQueries` loads the
 * catalog. `useProductVisibilityQueries` loads the
 * project's dashboard-ready runs (for the Run selector) and the product
 * visibility projection — defaulting to the latest product audit, sliced by
 * the engine filter via the backend's persisted per-engine aggregates.
 */
import { useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';

import { queryKeys } from '@/lib/api/query-keys';
import { productsApi } from '@/lib/api/products';
import { runsApi } from '@/lib/api/runs';
import {
  normalizeProductsTab,
  type ProductEngineFilter,
  type ProductsTab,
} from '@/lib/products/catalog';
import { toRunOptions } from '@/lib/visibility/dashboard';

export function useProductsTab() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const urlTab = normalizeProductsTab(searchParams?.get('tab'));

  const [activeTab, setActiveTab] = useState<ProductsTab>(urlTab);
  useEffect(() => {
    // Intentional URL→state sync (external navigation is the source of truth).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActiveTab(urlTab);
  }, [urlTab]);

  function selectTab(tab: ProductsTab) {
    setActiveTab(tab);
    const params = new URLSearchParams(searchParams?.toString() ?? '');
    params.set('tab', tab);
    router.replace(`${pathname}?${params.toString()}`);
  }

  return { activeTab, selectTab };
}

export function useCatalogQueries(projectId: string | null) {
  const productsQuery = useQuery({
    queryKey: queryKeys.products.list(projectId ?? ''),
    queryFn: ({ signal }) => productsApi.list(projectId!, { signal }),
    enabled: Boolean(projectId),
  });
  return { productsQuery };
}

export function useProductVisibilityQueries(projectId: string | null, enabled = true) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [engine, setEngine] = useState<ProductEngineFilter>('all');

  const auditsQuery = useQuery({
    queryKey: queryKeys.runs.list({ project_id: projectId ?? '' }),
    queryFn: ({ signal }) => runsApi.listAudits({ project_id: projectId! }, { signal }),
    // Only fetch on the Visibility tab — the Catalog tab never reads these.
    enabled: Boolean(projectId) && enabled,
  });
  const runOptions = useMemo(() => toRunOptions(auditsQuery.data ?? []), [auditsQuery.data]);

  // An explicit selection that still exists, else the latest (null = the
  // backend resolves the latest product audit itself).
  const activeRunId = useMemo(() => {
    if (selectedRunId && runOptions.some((run) => run.id === selectedRunId)) {
      return selectedRunId;
    }
    return null;
  }, [runOptions, selectedRunId]);

  const engineParam = engine === 'all' ? undefined : engine;
  const visibilityQuery = useQuery({
    queryKey: queryKeys.products.visibility(projectId ?? '', activeRunId ?? undefined, engineParam),
    queryFn: ({ signal }) =>
      productsApi.getProductVisibility(
        projectId!,
        { audit_id: activeRunId ?? undefined, engine: engineParam },
        { signal },
      ),
    enabled: Boolean(projectId) && enabled,
  });

  return {
    auditsQuery,
    runOptions,
    activeRunId,
    selectRun: setSelectedRunId,
    engine,
    setEngine,
    engineParam,
    visibilityQuery,
  };
}
