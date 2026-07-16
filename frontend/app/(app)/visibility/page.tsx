'use client';

import { PageTitle } from '@/components/ui/typography';
import { TooltipProvider } from '@/components/ui/tooltip';
import { VisibilityDashboard } from '@/components/visibility/visibility-dashboard';

/**
 * Visibility dashboard screen (F9, design.md §9.6).
 *
 * A selected-run projection over the active project's audits: a Visibility
 * Score header, a per-engine comparison for the selected run, and a
 * brand-vs-competitor rankings table (Visibility% / SOV% / Sentiment / Avg
 * Position). A run selector (defaults to the latest completed audit) plus
 * engine / prompt-type filters drive the query. Sentiment + Avg Position are
 * rendered but show the "—" not-yet-computed placeholder (decision B-2). No
 * cross-run trend chart at MVP (roadmap). Consumes the B6
 * `GET /projects/{id}/visibility?audit_id=` endpoint via `visibility.ts` and
 * the active project from the F5 context.
 */
export default function VisibilityPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <div>
          <PageTitle kicker="Analytics">Visibility</PageTitle>
          <p className="mt-1 max-w-2xl text-sm text-secondary">
            Your brand&apos;s visibility across AI answer engines for a single run — a Visibility
            Score, a per-engine comparison, and how you rank against competitors.
          </p>
        </div>
        <VisibilityDashboard />
      </div>
    </TooltipProvider>
  );
}
