'use client';

import { TooltipProvider } from '@/components/ui/tooltip';
import { SiteHealthScreen } from '@/components/site-health/site-health-screen';

/**
 * Site Health screen (Slice 7, mockups 708/709/712/713).
 *
 * Discover and analyze a project's pages for AI search optimization: Free gets a
 * server-selected sample; Starter discovers the full inventory, stages a
 * persistent monitored set, then analyzes it into a scored dashboard. Progress
 * is polling-first with an SSE invalidation accelerator; exports are
 * authenticated blob downloads. View actions are disabled until Slice 8.
 */
export default function SiteHealthPage() {
  return (
    <TooltipProvider>
      <SiteHealthScreen />
    </TooltipProvider>
  );
}
