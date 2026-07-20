'use client';

import type { ReactNode } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import type { SiteCrawl } from '@/lib/api/types';
import { crawlBadgeValue, statusLabel } from '@/lib/site-health/status';

/**
 * Stateless presentational pieces of the Site Health screen (header, skeleton,
 * and the empty / terminal phase cards). All behavior stays in
 * `site-health-screen.tsx`; these only render what they are handed.
 */

export function ScreenHeader({ actions }: Readonly<{ actions?: ReactNode }>) {
  if (!actions) return null;
  return <div className="flex flex-wrap items-center justify-end gap-3">{actions}</div>;
}

export function ScreenSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <Skeleton className="h-8 w-48" />
      <Card>
        <CardContent className="grid gap-3">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-40 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

/** First-run card: no crawl exists yet for this project. */
export function EmptyPhaseCard({
  onStart,
  startPending,
}: Readonly<{ onStart: () => void; startPending: boolean }>) {
  return (
    <Card>
      <CardContent className="grid gap-3 py-8 text-center">
        <p className="text-secondary text-sm">
          Discover and analyze your site&apos;s pages for AI search optimization.
        </p>
        <div className="flex justify-center">
          <Button onClick={onStart} disabled={startPending}>
            {startPending ? 'Starting…' : 'Start discovery'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Failed/cancelled crawl without score data: explicit stopped state + restart. */
export function TerminalPhaseCard({
  crawl,
  onStart,
  startPending,
}: Readonly<{ crawl: SiteCrawl; onStart: () => void; startPending: boolean }>) {
  return (
    <Card>
      <CardContent className="grid gap-3 py-8 text-center">
        <div className="flex justify-center">
          <Badge variant="run-status" value={crawlBadgeValue(crawl.status)}>
            {statusLabel(crawl.status)}
          </Badge>
        </div>
        <p className="text-secondary text-sm">
          {crawl.status === 'cancelled'
            ? 'This crawl was cancelled before it produced results.'
            : crawl.error_message || 'This crawl failed before it produced results.'}
        </p>
        <div className="flex justify-center">
          <Button onClick={onStart} disabled={startPending}>
            {startPending ? 'Starting…' : 'Start a new crawl'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
