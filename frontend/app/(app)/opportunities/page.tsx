'use client';

import { OpportunitiesScreen } from '@/components/opportunities/opportunities-screen';

/**
 * Opportunities (v1): the deterministic, priority-sorted action catalog for
 * the active project — summary strip (snapshot counts + Recompute + exports),
 * dense catalog table, and the evidence drawer drill-down. Detection and
 * scoring are entirely server-side; this screen only projects them.
 */
export default function OpportunitiesPage() {
  return <OpportunitiesScreen />;
}
