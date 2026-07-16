import Link from 'next/link';
import { Rocket } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

/**
 * Empty state for a project with no completed runs (design.md §9.6): a
 * "Launch your first audit" card linking to `/runs`. The dashboard is a
 * projection over completed audits, so there is nothing to render until one
 * finishes.
 */
export function VisibilityEmptyState() {
  return (
    <Card>
      <CardContent className="grid justify-items-center gap-4 py-12 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-accent-subtle text-accent-text">
          <Rocket className="size-6" aria-hidden />
        </span>
        <div className="grid gap-1">
          <h2 className="text-lg font-semibold text-foreground">No completed runs yet</h2>
          <p className="max-w-md text-sm text-secondary">
            Launch an audit to see how AI answer engines talk about your brand. Once a run
            completes, its Visibility Score, per-engine comparison and rankings show up here.
          </p>
        </div>
        <Button asChild variant="primary" size="md">
          <Link href="/runs">Launch your first audit</Link>
        </Button>
      </CardContent>
    </Card>
  );
}
