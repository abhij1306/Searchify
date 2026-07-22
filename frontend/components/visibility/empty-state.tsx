import Link from 'next/link';
import { Rocket } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow } from '@/components/ui/card';
import { IconChip } from '@/components/ui/icon-chip';
import { displayHeadingLgClasses } from '@/components/ui/typography';

/**
 * Empty state for a project with no completed runs (design.md §9.6): a
 * "Launch your first audit" card linking to `/runs`, in the midnight
 * empty-state pattern (mono eyebrow + display heading + ghost CTA). The
 * dashboard is a projection over completed audits, so there is nothing to
 * render until one finishes. When a run is already in progress
 * (`hasActiveRun`), the copy and CTA switch from "launch one" to "one is on
 * its way".
 */
export function VisibilityEmptyState({
  hasActiveRun = false,
}: Readonly<{ hasActiveRun?: boolean }>) {
  return (
    <Card>
      <CardContent className="grid justify-items-center gap-4 py-12 text-center">
        <CardEyebrow>Visibility</CardEyebrow>
        <IconChip>
          <Rocket className="size-6" aria-hidden />
        </IconChip>
        <div className="grid gap-1">
          <h2 className={displayHeadingLgClasses}>No completed runs yet</h2>
          <p className="text-secondary max-w-md text-sm">
            {hasActiveRun
              ? 'An audit is running now. Once it completes, its Visibility Score, per-engine comparison and rankings show up here automatically.'
              : 'Launch an audit to see how AI answer engines talk about your brand. Once a run completes, its Visibility Score, per-engine comparison and rankings show up here.'}
          </p>
        </div>
        <Button asChild variant="ghost" size="md">
          <Link href="/runs">{hasActiveRun ? 'View runs' : 'Launch your first audit'}</Link>
        </Button>
      </CardContent>
    </Card>
  );
}
