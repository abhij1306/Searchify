import Link from 'next/link';
import { Plug } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow } from '@/components/ui/card';
import { IconChip } from '@/components/ui/icon-chip';
import { displayHeadingLgClasses } from '@/components/ui/typography';

/**
 * Empty state for a project with no persisted Traffic snapshot (mockup
 * `analytics-dashboards-traffic-empty.html`), in the `VisibilityEmptyState`
 * pattern (mono eyebrow + icon chip + display heading + ghost CTA). Traffic
 * renders persisted sync projections only, so there is nothing to show until
 * an integration syncs. When connections already exist (`hasConnections`) the
 * copy switches from "connect one" to "the first sync is on its way" — the
 * CTA lands on Settings → Integrations either way.
 */
export function TrafficEmptyState({
  hasConnections = false,
}: Readonly<{ hasConnections?: boolean }>) {
  return (
    <Card>
      <CardContent className="grid justify-items-center gap-4 py-12 text-center">
        <CardEyebrow>Traffic</CardEyebrow>
        <IconChip>
          <Plug className="size-6" aria-hidden />
        </IconChip>
        <div className="grid gap-1">
          <h2 className={displayHeadingLgClasses}>
            {hasConnections ? 'Your first sync is on its way' : 'Connect search data to see traffic'}
          </h2>
          <p className="text-secondary max-w-md text-sm">
            {hasConnections
              ? 'Traffic projects your Google Search Console and Google Analytics 4 data into organic and AI-driven impressions, clicks, sessions, and conversions. Your integrations are connected — the first syncs project here once they complete.'
              : 'Traffic projects your Google Search Console and Google Analytics 4 data into organic and AI-driven impressions, clicks, sessions, and conversions. Connect an integration to start syncing.'}
          </p>
        </div>
        <Button asChild variant="ghost" size="md">
          <Link href="/settings?tab=integrations">
            {hasConnections ? 'Open integrations' : 'Connect an integration'}
          </Link>
        </Button>
      </CardContent>
    </Card>
  );
}
