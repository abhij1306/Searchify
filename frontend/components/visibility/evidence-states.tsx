'use client';

import Link from 'next/link';
import { AlertTriangle, Info, Inbox, RefreshCw, SearchX } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

/**
 * Shared data-state presentations for the two evidence tabs (design.md states
 * gallery): loading skeleton, retryable error, empty (no executions yet),
 * filtered-empty, and the truncation notice. Both `mentions-citations.tsx` and
 * `fanout-evidence.tsx` reuse these so their states stay consistent.
 */

import type { UseQueryResult } from '@tanstack/react-query';

import type { VisibilityEvidenceResponse } from '@/lib/api/types';

/** Props shared by both evidence tabs (Query Fanout, Mentions & Citations). */
export type EvidenceTabProps = Readonly<{
  query: UseQueryResult<VisibilityEvidenceResponse, unknown>;
  isFiltered: boolean;
  onClearFilters?: () => void;
  limit: number;
}>;

export function EvidenceSkeleton({ title }: Readonly<{ title: string }>) {
  return (
    <Card aria-hidden>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        <Skeleton className="h-24 w-full" />
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-3">
            <Skeleton className="h-4 flex-1" />
            <Skeleton className="h-4 w-12" />
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export function EvidenceError({
  title,
  onRetry,
}: Readonly<{ title: string; onRetry: () => void }>) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid justify-items-center gap-3 py-10 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-danger-bg text-danger-text">
            <AlertTriangle className="size-5" aria-hidden />
          </span>
          <h3 className="text-base font-semibold text-foreground">
            Couldn&apos;t load this evidence
          </h3>
          <p className="max-w-xs text-sm text-secondary">
            The request failed or timed out. Your filters are unchanged.
          </p>
          <Button variant="primary" size="sm" onClick={onRetry}>
            <RefreshCw className="size-4" aria-hidden />
            Retry
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function EvidenceEmpty({
  title,
  heading,
  body,
}: Readonly<{ title: string; heading: string; body: string }>) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid justify-items-center gap-3 py-10 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-neutral-bg text-muted">
            <Inbox className="size-5" aria-hidden />
          </span>
          <h3 className="text-base font-semibold text-foreground">{heading}</h3>
          <p className="max-w-sm text-sm text-secondary">{body}</p>
          <Button asChild variant="secondary" size="sm">
            <Link href="/runs">View Runs</Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function EvidenceFilteredEmpty({
  title,
  body,
  onClear,
}: Readonly<{ title: string; body: string; onClear?: () => void }>) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid justify-items-center gap-3 py-10 text-center">
          <span className="flex size-10 items-center justify-center rounded-full bg-neutral-bg text-muted">
            <SearchX className="size-5" aria-hidden />
          </span>
          <h3 className="text-base font-semibold text-foreground">
            No results match these filters
          </h3>
          <p className="max-w-sm text-sm text-secondary">{body}</p>
          {onClear ? (
            <Button variant="secondary" size="sm" onClick={onClear}>
              Clear filters
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function TruncationNotice({ limit }: Readonly<{ limit: number }>) {
  return (
    <div className="flex items-center gap-2 border-t border-border-subtle px-4 py-2.5 text-xs text-muted">
      <Info className="size-3.5 shrink-0" aria-hidden />
      <span>
        Showing newest {limit} executions; refine filters to narrow results.
      </span>
    </div>
  );
}
