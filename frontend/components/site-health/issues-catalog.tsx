'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/typography';
import { siteHealthQueries, type IssuesParams } from '@/lib/api/site-health';
import type { IssueDimension, SiteIssue } from '@/lib/api/types';
import {
  SUMMARY_SEVERITIES,
  dimensionLabel,
  issueTitle,
  severityBadgeValue,
  severityLabel,
} from '@/lib/site-health/issues';
import { cn } from '@/lib/utils';

const ISSUE_LIMIT = 25;

/** Filter chips (mockup 710): All + severity tiers + technical/AEO dimension. */
type FilterKey = 'all' | 'high' | 'medium' | 'low' | 'technical' | 'aeo';

const FILTERS: ReadonlyArray<{ key: FilterKey; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'high', label: 'High' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
  { key: 'technical', label: 'Technical' },
  { key: 'aeo', label: 'AEO' },
];

/** Translate a filter chip into server-side issue query params. */
function filterParams(filter: FilterKey): Pick<IssuesParams, 'severity' | 'dimension'> {
  switch (filter) {
    case 'high':
    case 'medium':
    case 'low':
      return { severity: filter };
    case 'technical':
    case 'aeo':
      return { dimension: filter };
    default:
      return {};
  }
}

/**
 * Grouped Issues catalog (Slice 8, mockup 710).
 *
 * Renders the API-owned occurrence / severity / affected-page summary, a
 * search box + severity/dimension filter chips (server-backed, never a
 * client-side filter over the current page), then keyset-paginated grouped
 * issue cards with remediation, a "View affected URLs" navigation, and a
 * client-only "Copy fix prompt". The catalog title uses the current display
 * label with a `rule_id` fallback. There is no unsupported "mark reviewed"
 * persistence — the backend has no such state.
 */
export function IssuesCatalog({ crawlId }: Readonly<{ crawlId: string }>) {
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<FilterKey>('all');
  const [cursorStack, setCursorStack] = useState<string[]>([]);

  const cursor = cursorStack.at(-1);
  const params: IssuesParams = useMemo(
    () => ({
      ...filterParams(filter),
      query: query.trim() || undefined,
      cursor,
      limit: ISSUE_LIMIT,
    }),
    [filter, query, cursor],
  );

  const issuesQuery = useQuery(siteHealthQueries.issues(crawlId, params));
  const summary = issuesQuery.data?.summary ?? null;
  const rows = issuesQuery.data?.items ?? [];
  const nextCursor = issuesQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack.length > 0;

  const applySearch = (event: React.FormEvent) => {
    event.preventDefault();
    setQuery(search);
    setCursorStack([]);
  };
  const selectFilter = (next: FilterKey) => {
    setFilter(next);
    setCursorStack([]);
  };
  const goNext = () => {
    if (nextCursor) setCursorStack((prev) => [...prev, nextCursor]);
  };
  const goPrev = () => setCursorStack((prev) => prev.slice(0, -1));

  return (
    <div className="grid gap-6">
      <SummaryTiles summary={summary} loading={issuesQuery.isLoading} />

      <form onSubmit={applySearch} className="flex flex-wrap items-center gap-2">
        <Input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search issues…"
          aria-label="Search issues"
          className="max-w-xs"
        />
        <div className="flex flex-wrap items-center gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => selectFilter(f.key)}
              aria-pressed={f.key === filter}
              className={cn(
                'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                f.key === filter
                  ? 'border-accent bg-accent-subtle text-foreground'
                  : 'border-border text-secondary hover:text-foreground',
              )}
            >
              {f.label}
              {f.key === 'all' && summary ? ` (${summary.issue_count})` : ''}
            </button>
          ))}
        </div>
      </form>

      {issuesQuery.isError ? (
        <Alert tone="danger">Could not load issues for this crawl. Please refresh.</Alert>
      ) : issuesQuery.isLoading ? (
        <div className="grid gap-4">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <Card>
          <CardContent className="text-sm text-secondary">
            No issues match this view.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {rows.map((issue) => (
            <IssueCard key={issue.id} issue={issue} crawlId={crawlId} />
          ))}
        </div>
      )}

      {rows.length > 0 ? (
        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={goPrev} disabled={!canPrev}>
            Previous
          </Button>
          <Button variant="secondary" size="sm" onClick={goNext} disabled={!nextCursor}>
            Next
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function SummaryTiles({
  summary,
  loading,
}: Readonly<{ summary: import('@/lib/api/types').IssuesSummary | null; loading: boolean }>) {
  if (loading && !summary) {
    return (
      <div className="grid gap-3 sm:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    );
  }
  if (!summary) return null;
  const high = summary.severity_counts.high ?? 0;
  const critical = summary.severity_counts.critical ?? 0;
  const tiles = [
    { label: 'Total Issues', value: `${summary.issue_count}`, tone: 'text-foreground' },
    { label: 'High Severity', value: `${high + critical}`, tone: 'text-danger-text' },
    { label: 'Medium', value: `${summary.severity_counts.medium ?? 0}`, tone: 'text-warning-text' },
    { label: 'Low', value: `${summary.severity_counts.low ?? 0}`, tone: 'text-info-text' },
    {
      label: 'Pages Affected',
      value: `${summary.monitored_affected_url_count}`,
      sub: `/ ${summary.affected_url_count}`,
      tone: 'text-foreground',
    },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-5">
      {tiles.map((tile) => (
        <Card key={tile.label}>
          <CardContent className="grid gap-1 py-4">
            <Label>{tile.label}</Label>
            <span className={cn('mono text-2xl font-semibold', tile.tone)}>
              {tile.value}
              {tile.sub ? <span className="ml-1 text-sm text-muted">{tile.sub}</span> : null}
            </span>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function IssueCard({ issue, crawlId }: Readonly<{ issue: SiteIssue; crawlId: string }>) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const detailQuery = useQuery({
    ...siteHealthQueries.issue(crawlId, issue.id, { limit: 25 }),
    enabled: expanded,
  });
  const affected = detailQuery.data?.affected_urls ?? [];

  const copyPrompt = async () => {
    const prompt = buildFixPrompt(issue);
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      setTimeout(() => setCopied(false), 1_500);
    } catch {
      // Clipboard may be unavailable (permissions / insecure context); no-op.
    }
  };

  return (
    <Card>
      <CardContent className="grid gap-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="status" value={severityBadgeValue(issue.severity)}>
              {severityLabel(issue.severity)}
            </Badge>
            <DimensionBadge dimension={issue.dimension} />
          </div>
          <span className="whitespace-nowrap text-xs text-muted">
            {issue.affected_url_count} {issue.affected_url_count === 1 ? 'page' : 'pages'} affected
          </span>
        </div>

        <div className="grid gap-1">
          <h3 className="text-base font-semibold text-foreground">{issueTitle(issue)}</h3>
        </div>

        {issue.remediation ? (
          <div className="rounded-md border border-border-subtle bg-background-alt p-3">
            <Label className="mb-1 block">Remediation</Label>
            <p className="whitespace-pre-line text-sm text-secondary">{issue.remediation}</p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={() => setExpanded((prev) => !prev)}
            aria-expanded={expanded}
          >
            {expanded ? 'Hide affected URLs' : 'View affected URLs'}
          </Button>
          <Button variant="secondary" size="sm" onClick={copyPrompt}>
            {copied ? 'Copied' : 'Copy fix prompt'}
          </Button>
        </div>

        {expanded ? (
          <div className="rounded-md border border-border-subtle">
            {detailQuery.isError ? (
              <div className="p-3">
                <Alert tone="danger">Could not load affected URLs.</Alert>
              </div>
            ) : detailQuery.isLoading ? (
              <div className="grid gap-2 p-3">
                <Skeleton className="h-6 w-full" />
                <Skeleton className="h-6 w-full" />
              </div>
            ) : affected.length === 0 ? (
              <p className="p-3 text-sm text-secondary">No affected URLs found.</p>
            ) : (
              <ul className="divide-y divide-border-subtle">
                {affected.map((url) => (
                  <li key={url.site_url_id} className="px-3 py-2">
                    <Link
                      href={`/site-health/crawls/${crawlId}/pages/${url.site_url_id}`}
                      className="flex flex-col gap-0.5 hover:text-accent"
                    >
                      <span className="text-sm font-medium text-foreground">
                        {url.title ?? url.display_url}
                      </span>
                      <span className="mono text-2xs text-muted">{url.display_url}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** AEO / Technical dimension chip. Uses the neutral badge with tinted text. */
function DimensionBadge({ dimension }: Readonly<{ dimension: IssueDimension }>) {
  return (
    <Badge
      className={cn(
        'capitalize',
        dimension === 'aeo' ? 'text-accent' : 'text-info-text',
      )}
    >
      {dimensionLabel(dimension)}
    </Badge>
  );
}

/** Build a client-only remediation prompt to paste into an AI assistant. */
function buildFixPrompt(issue: SiteIssue): string {
  const label = issueTitle(issue);
  const lines = [
    `Fix this Site Health issue on my website: "${label}" (${dimensionLabel(issue.dimension)}, ${severityLabel(issue.severity)} severity).`,
  ];
  if (issue.remediation) {
    lines.push('', 'Recommended remediation:', issue.remediation);
  }
  return lines.join('\n');
}
