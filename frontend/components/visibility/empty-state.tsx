import Link from 'next/link';
import { Rocket } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

/**
 * Empty state for a project with no completed runs (design.md §9.6): a
 * "Launch your first audit" card linking to `/runs`. The dashboard is a
 * projection over completed audits, so there is nothing to render until one
 * finishes. When a run is already in progress (`hasActiveRun`), the copy and
 * CTA switch from "launch one" to "one is on its way".
 */
export function VisibilityEmptyState({
  hasActiveRun = false,
}: Readonly<{ hasActiveRun?: boolean }>) {
  return (
    <Card>
      <CardContent className="grid justify-items-center gap-4 py-12 text-center">
        <span className="bg-accent-subtle text-accent-text flex size-12 items-center justify-center rounded-full">
          <Rocket className="size-6" aria-hidden />
        </span>
        <div className="grid gap-1">
          <h2 className="text-foreground text-lg font-semibold">No completed runs yet</h2>
          <p className="text-secondary max-w-md text-sm">
            {hasActiveRun
              ? 'Your first audit is running now. Once it completes, its Visibility Score, per-engine comparison and rankings show up here automatically.'
              : 'Launch an audit to see how AI answer engines talk about your brand. Once a run completes, its Visibility Score, per-engine comparison and rankings show up here.'}
          </p>
        </div>
        <Button asChild variant="primary" size="md">
          <Link href="/runs">{hasActiveRun ? 'View runs' : 'Launch your first audit'}</Link>
        </Button>
      </CardContent>
    </Card>
  );
}
