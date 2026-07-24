'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { badgeBase, neutralBadge } from '@/components/ui/badge-variants';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { CursorPager } from '@/components/ui/cursor-pager';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { analyticsApi, type AiSource, type AnalyticsReferralRow } from '@/lib/api/analytics';
import { queryKeys } from '@/lib/api/query-keys';
import {
  AI_SOURCE_FILL,
  AI_SOURCES,
  REFERRALS_PAGE_SIZE,
  aiSourceLabel,
  formatOccurredAt,
  splitLandingUrl,
} from '@/lib/analytics/series';
import { useCursorStack } from '@/lib/site-health/use-cursor-stack';
import { cn } from '@/lib/utils';

// Midnight filter-chip language (visibility-toolbar idiom): a non-default
// filter value flips the chip to the accent-soft active state.
const CHIP_ACTIVE_CLASS =
  'border-accent-border bg-accent-soft text-accent-text hover:border-accent-border hover:bg-accent-soft hover:text-accent-text';

/**
 * AI-referral events drill-down (F9): the dense keyset-paged table of
 * persisted `ReferralClassification` + `ReferralEvent` rows, newest first.
 * Columns per the mockup: Time (mono), Landing URL (muted host + path),
 * Referrer host, per-source badge, Confidence, Match signal (`—` for nulls —
 * never a fabricated value). Paging walks the C4 keyset envelope via the
 * shared `useCursorStack`; the `?source=` filter restarts the walk. The
 * parent remounts this table on window change (`key={from|to}`) so a stale
 * cursor is never replayed against a different window (backend rejects that
 * with a 400).
 */
export function ReferralsTable({
  projectId,
  from,
  to,
}: Readonly<{ projectId: string; from: string | undefined; to: string | undefined }>) {
  const [source, setSource] = useState<AiSource | null>(null);
  const { cursor, canPrev, push, pop, reset } = useCursorStack();

  const referralsQuery = useQuery({
    queryKey: queryKeys.analytics.referrals(projectId, {
      source: source ?? null,
      from: from ?? null,
      to: to ?? null,
      cursor: cursor ?? null,
    }),
    queryFn: ({ signal }) =>
      analyticsApi.getReferrals(
        projectId,
        { source: source ?? undefined, from, to, cursor },
        { signal },
      ),
  });

  // A new source filter restarts the keyset walk from page one.
  function selectSource(next: AiSource | null) {
    setSource(next);
    reset();
  }

  const rows = referralsQuery.data?.items ?? [];
  const nextCursor = referralsQuery.data?.next_cursor ?? null;

  let body: React.ReactNode;
  if (referralsQuery.isLoading) {
    body = (
      <CardContent className="grid gap-2" aria-hidden>
        {[0, 1, 2, 3].map((index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </CardContent>
    );
  } else if (referralsQuery.isError) {
    body = (
      <CardContent>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-secondary text-sm">
            Could not load referral events. Check your connection and try again.
          </p>
          <Button variant="secondary" size="sm" onClick={() => referralsQuery.refetch()}>
            Retry
          </Button>
        </div>
      </CardContent>
    );
  } else if (rows.length === 0) {
    body = (
      <CardContent className="grid justify-items-center gap-2 py-10 text-center">
        <p className="text-secondary text-sm">
          {source === null
            ? 'No AI-referral events recorded in this window yet.'
            : `No referral events match ${aiSourceLabel(source)}.`}
        </p>
        {source !== null ? (
          <Button variant="ghost" size="sm" onClick={() => selectSource(null)}>
            Clear source filter
          </Button>
        ) : null}
      </CardContent>
    );
  } else {
    body = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Time</TableHead>
            <TableHead>Landing URL</TableHead>
            <TableHead>Referrer host</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Confidence</TableHead>
            <TableHead>Match signal</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <ReferralRow key={row.id} row={row} />
          ))}
        </TableBody>
      </Table>
    );
  }

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div className="grid gap-1">
          <CardTitle>AI-referral events</CardTitle>
          <CardDescription>Classified referral sessions, most recent first</CardDescription>
        </div>
        <Dropdown>
          <DropdownTrigger asChild>
            <Button
              variant="secondary"
              size="sm"
              aria-label="Filter by source"
              className={cn(source !== null && CHIP_ACTIVE_CLASS)}
            >
              <span className="text-muted">Source:</span>
              <span className="font-medium">
                {source === null ? 'All sources' : aiSourceLabel(source)}
              </span>
              <ChevronDown className="text-muted size-3" aria-hidden />
            </Button>
          </DropdownTrigger>
          <DropdownContent>
            <DropdownLabel>Source</DropdownLabel>
            <DropdownItem data-active={source === null} onSelect={() => selectSource(null)}>
              All sources
            </DropdownItem>
            {AI_SOURCES.map((option) => (
              <DropdownItem
                key={option}
                data-active={source === option}
                onSelect={() => selectSource(option)}
              >
                {aiSourceLabel(option)}
              </DropdownItem>
            ))}
          </DropdownContent>
        </Dropdown>
      </CardHeader>
      {body}
      <div className="border-border-subtle flex items-center justify-between gap-3 border-t px-3 py-2">
        <span className="text-2xs text-muted font-mono">{REFERRALS_PAGE_SIZE} rows per page</span>
        <span className="flex gap-2">
          <CursorPager
            canPrev={canPrev}
            canNext={Boolean(nextCursor)}
            onPrev={pop}
            onNext={() => push(nextCursor)}
          />
        </span>
      </div>
    </Card>
  );
}

function ReferralRow({ row }: Readonly<{ row: AnalyticsReferralRow }>) {
  const landing = splitLandingUrl(row.landing_url);
  return (
    <TableRow>
      <TableCell>
        <span className="mono text-xs">{formatOccurredAt(row.occurred_at)}</span>
      </TableCell>
      <TableCell>
        <span className="mono text-xs break-all">
          {landing.host ? <span className="text-muted">{landing.host}</span> : null}
          {landing.rest}
        </span>
      </TableCell>
      <TableCell>
        {row.referrer_host ? (
          <span className="mono text-xs">{row.referrer_host}</span>
        ) : (
          <span className="text-subtle">—</span>
        )}
      </TableCell>
      <TableCell>
        <span className={cn(badgeBase, neutralBadge)}>
          <span
            className={cn('size-1.5 rounded-full', AI_SOURCE_FILL[row.ai_source])}
            aria-hidden
          />
          {aiSourceLabel(row.ai_source)}
        </span>
      </TableCell>
      <TableCell>
        <Badge variant="status" value={row.confidence === 'exact' ? 'info' : 'warning'}>
          {row.confidence}
        </Badge>
      </TableCell>
      <TableCell>
        {row.match_signal ? (
          <span className="mono text-secondary text-xs">{row.match_signal}</span>
        ) : (
          <span className="text-subtle">—</span>
        )}
      </TableCell>
    </TableRow>
  );
}
