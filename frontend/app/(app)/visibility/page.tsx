'use client';

import { Suspense } from 'react';

import { TooltipProvider } from '@/components/ui/tooltip';
import { DashboardSkeleton } from '@/components/visibility/dashboard-skeleton';
import { VisibilityDashboard } from '@/components/visibility/visibility-dashboard';

/**
 * Visibility workspace screen (F9, four-tab IA).
 *
 * One workspace shell with a shared filter bar above an accessible tablist and
 * exactly four focused panels:
 *   - **Overview** (default): the selected-run Visibility Score, both SOV
 *     definitions, per-engine / logical-engine comparison, and brand-vs-
 *     competitor rankings, from `GET /projects/{id}/visibility?audit_id=`.
 *   - **Trends**: cross-run Visibility Score, Share of Voice, and ranking
 *     movement across completed audits, with engine / date / granularity
 *     controls and version-boundary markers, from
 *     `GET /projects/{id}/visibility/trends`.
 *   - **Mentions & Citations**: persisted brand/competitor mentions and
 *     classified citation records with task/analysis/artifact provenance.
 *   - **Query Fanout**: frozen prompts, provider-generated search queries, and
 *     search-count / text-availability states.
 * The two evidence tabs share the persisted
 * `GET /projects/{id}/visibility/evidence` dataset. Sentiment + Avg Position
 * stay the "—" not-yet-computed placeholder (decision B-2). There are no
 * Sources, Topics, or Sentiment tabs. All endpoints go through `visibility.ts`,
 * scoped to the active project from the F5 context. The page title renders in
 * the top bar (F5), so there is no in-page header block.
 */
export default function VisibilityPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <Suspense fallback={<DashboardSkeleton />}>
          <VisibilityDashboard />
        </Suspense>
      </div>
    </TooltipProvider>
  );
}
