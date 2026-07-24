'use client';

import { Suspense } from 'react';

import { TrafficScreen, TrafficSkeleton } from '@/components/traffic/traffic-screen';
import { TooltipProvider } from '@/components/ui/tooltip';

/**
 * Traffic screen (F6/F7) — organic + AI-driven traffic projected from the
 * synced Google Search Console / GA4 integrations: headline stat cards,
 * impressions/clicks/CTR/position trends, and the top-pages / top-queries
 * keyset tables, with a Sync-now pass-through that polls the enqueued
 * integration runs. All endpoints go through `traffic.ts` / `integrations.ts`,
 * scoped to the active project from the project context. The page title
 * renders in the top bar, so there is no in-page header block.
 */
export default function TrafficPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <Suspense fallback={<TrafficSkeleton />}>
          <TrafficScreen />
        </Suspense>
      </div>
    </TooltipProvider>
  );
}
