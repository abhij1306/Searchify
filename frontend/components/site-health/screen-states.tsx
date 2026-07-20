'use client';

import type { ReactNode } from 'react';

import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

/**
 * Stateless presentational pieces of the Site Health screen (header + loading
 * skeleton). All behavior stays in `site-health-screen.tsx`; these only render
 * what they are handed. The empty / terminal lifecycle states are in-section
 * content of the canonical layout (`StatusStrip` / `InventorySection`), not
 * separate cards — the screen never swaps panels.
 */

export function ScreenHeader({ actions }: Readonly<{ actions?: ReactNode }>) {
  if (!actions) return null;
  return <div className="flex flex-wrap items-center justify-end gap-3">{actions}</div>;
}

export function ScreenSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <Skeleton className="h-8 w-48" />
      <Card>
        <CardContent className="grid gap-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-40 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}
