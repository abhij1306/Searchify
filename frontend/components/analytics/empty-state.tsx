import Link from 'next/link';
import { BarChart3 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow } from '@/components/ui/card';
import { IconChip } from '@/components/ui/icon-chip';
import { displayHeadingLgClasses } from '@/components/ui/typography';

/**
 * Empty state for `/analytics` with no AI-referral data yet (mockup
 * `analytics-dashboards-llm-empty`): the midnight empty-state pattern (mono
 * eyebrow + icon chip + display heading + ghost CTA) in the
 * `VisibilityEmptyState` style. The CTA lands on Settings → Integrations —
 * the GA4 connection whose referral sessions (with completed-audit
 * visibility snapshots) feed this screen.
 */
export function AnalyticsEmptyState() {
  return (
    <Card>
      <CardContent className="grid justify-items-center gap-4 py-12 text-center">
        <CardEyebrow>LLM Analytics</CardEyebrow>
        <IconChip>
          <BarChart3 className="size-6" aria-hidden />
        </IconChip>
        <div className="grid gap-1">
          <h2 className={displayHeadingLgClasses}>No AI-referral data yet</h2>
          <p className="text-secondary max-w-md text-sm">
            Once a Google Analytics 4 integration lands referral sessions and completed audits
            supply visibility snapshots, AI-referral volume, per-source breakdowns, and the
            visibility↔referral correlation show up here.
          </p>
        </div>
        <Button asChild variant="ghost" size="md">
          <Link href="/settings?tab=integrations">Open integration settings</Link>
        </Button>
      </CardContent>
    </Card>
  );
}
