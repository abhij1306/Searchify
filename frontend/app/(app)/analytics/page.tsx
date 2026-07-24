'use client';

import { Suspense } from 'react';

import { AnalyticsScreen, AnalyticsSkeleton } from '@/components/analytics/analytics-screen';
import { TooltipProvider } from '@/components/ui/tooltip';

/**
 * LLM Analytics screen (F8 + F9) — the AEO Insights dashboard: AI-referral
 * volume/share trends, per-source breakdown, visibility↔referral correlation,
 * cross-engine visibility, theme rollup, and the referrals drill-down. All
 * endpoints go through `analyticsApi` (`lib/api/analytics.ts`), scoped to the
 * active project from the app-shell context; the page title renders in the
 * top bar, so there is no in-page header block (shell precedent:
 * `/visibility`).
 */
export default function AnalyticsPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <Suspense fallback={<AnalyticsSkeleton />}>
          <AnalyticsScreen />
        </Suspense>
      </div>
    </TooltipProvider>
  );
}
