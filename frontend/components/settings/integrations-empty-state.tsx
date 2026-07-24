'use client';

import { Unplug } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow } from '@/components/ui/card';
import { IconChip } from '@/components/ui/icon-chip';
import { displayHeadingLgClasses } from '@/components/ui/typography';
import { integrationsApi } from '@/lib/api/integrations';
import { assignLocation } from '@/lib/navigate';

/**
 * Empty state for the Settings → Integrations tab when the workspace has no
 * connections yet (mockup `integrations-settings-empty-first-run-*.html`), in
 * the `VisibilityEmptyState` pattern (mono eyebrow + IconChip + display
 * heading + CTAs).
 *
 * Both CTAs are full-page navigations to the same-origin OAuth start
 * endpoints (302s — never apiClient fetches): one Google consent links Search
 * Console + Analytics 4 on a shared grant; Microsoft links Bing Webmaster
 * Tools.
 */
export function IntegrationsEmptyState() {
  return (
    <Card data-testid="integrations-empty-state">
      <CardContent className="grid justify-items-center gap-4 py-12 text-center">
        <CardEyebrow>Integrations</CardEyebrow>
        <IconChip>
          <Unplug className="size-6" aria-hidden />
        </IconChip>
        <div className="grid gap-1">
          <h2 className={displayHeadingLgClasses}>No integrations connected</h2>
          <p className="text-secondary max-w-md text-sm">
            Connect Google to sync Search Console and Analytics 4 on one shared OAuth grant, or
            connect Microsoft for Bing Webmaster Tools. Synced data powers Traffic and LLM
            Analytics.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="md" onClick={() => assignLocation(integrationsApi.oauthStartUrl('gsc'))}>
            Connect Google
          </Button>
          <Button
            variant="ghost"
            size="md"
            onClick={() => assignLocation(integrationsApi.oauthStartUrl('bing'))}
          >
            Connect Microsoft
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
