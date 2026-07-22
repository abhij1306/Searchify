'use client';

import { MinusCircle } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { eyebrowClasses } from '@/components/ui/eyebrow';
import {
  EvidenceEmpty,
  EvidenceError,
  EvidenceFilteredEmpty,
  EvidenceSkeleton,
  TruncationNotice,
  type EvidenceTabProps,
} from '@/components/visibility/evidence-states';
import { engineLabel } from '@/lib/providers/catalog';
import { cn } from '@/lib/utils';
import type { VisibilityExecutionEvidence } from '@/lib/api/types';
import {
  countOnlyExplanation,
  formatExecutionDate,
  groupByPrompt,
  provenanceSummary,
  queryTexts,
  type PromptGroup,
} from '@/lib/visibility/evidence';

const TITLE = 'Query Fanout';

/**
 * Query Fanout tab — frozen prompt text, provider-generated search queries,
 * search counts, and text-availability state per execution, CLIENT-grouped by
 * the frozen prompt for presentation only. It never claims a global prompt
 * total, a true average over the truncated window, or numbered pagination the
 * endpoint cannot support, and it does NOT duplicate the citation browser (that
 * lives in Mentions & Citations).
 *
 * Per-execution query states are distinct (plan §Query Fanout / states gallery):
 *   - `queries_available` → the actual stored query strings;
 *   - `count_only`        → "Query text unavailable; provider reported N searches";
 *   - `no_search`         → "No web searches performed for this execution".
 *
 * States: skeleton, retryable error, empty, filtered-empty, and a truncation
 * notice when the newest window overflowed.
 */
export function FanoutEvidence({ query, isFiltered, onClearFilters, limit }: EvidenceTabProps) {
  if (query.isLoading) {
    return <EvidenceSkeleton title={TITLE} />;
  }
  if (query.isError) {
    return <EvidenceError title={TITLE} onRetry={() => query.refetch()} />;
  }

  const items = query.data?.items ?? [];
  const truncated = query.data?.truncated ?? false;

  if (items.length === 0) {
    return isFiltered ? (
      <EvidenceFilteredEmpty
        title={TITLE}
        body="No executions match the selected run, engine, prompt, and date range. Widen the range or clear a filter."
        onClear={onClearFilters}
      />
    ) : (
      <EvidenceEmpty
        title={TITLE}
        heading="No query fanout yet"
        body="Once a run executes your prompts, the search queries each engine generated (and where text is unavailable) appear here, grouped by prompt."
      />
    );
  }

  const groups = groupByPrompt(items);

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{TITLE}</CardTitle>
          <p className="text-secondary text-sm">Per-execution search queries grouped by prompt.</p>
        </div>
        <Badge variant="neutral">
          {groups.length} {groups.length === 1 ? 'prompt' : 'prompts'}
        </Badge>
      </CardHeader>
      <CardContent className="grid gap-5 p-0">
        <div className="grid gap-5 p-[var(--card-padding)]">
          {groups.map((group) => (
            <PromptGroupBlock key={group.promptSnapshotId} group={group} />
          ))}
        </div>
        {truncated ? <TruncationNotice limit={limit} /> : null}
      </CardContent>
    </Card>
  );
}

function PromptGroupBlock({ group }: Readonly<{ group: PromptGroup }>) {
  return (
    <section className="grid gap-2.5">
      <h3 className="text-foreground text-sm font-semibold">{group.promptText}</h3>
      <ul className="grid gap-2.5">
        {group.executions.map((item) => (
          <ExecutionRow key={item.analysis_id} item={item} />
        ))}
      </ul>
    </section>
  );
}

function ExecutionRow({ item }: Readonly<{ item: VisibilityExecutionEvidence }>) {
  return (
    <li className="border-border-subtle bg-background-alt grid gap-2 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted text-xs">
          Execution #{item.prompt_index} · rep {item.repetition} ·{' '}
          {formatExecutionDate(item.completed_at)}
        </p>
        <span className="flex items-center gap-2">
          <Badge variant="neutral">{engineLabel(item.logical_engine)}</Badge>
          <span className="text-muted text-xs">{item.transport_model}</span>
          <span className="mono text-secondary text-xs">
            {item.search_query_count} {item.search_query_count === 1 ? 'search' : 'searches'}
          </span>
        </span>
      </div>

      <p className={cn(eyebrowClasses, 'font-normal')}>{provenanceSummary(item)}</p>

      <QueryDetail item={item} />
    </li>
  );
}

function QueryDetail({ item }: Readonly<{ item: VisibilityExecutionEvidence }>) {
  if (item.state === 'queries_available') {
    const queries = queryTexts(item);
    return (
      <ul className="grid gap-1.5">
        {queries.map((query, index) => (
          <li
            key={`${index}-${query}`}
            className="border-border-subtle bg-panel text-secondary rounded-md border px-3 py-2 font-mono text-xs"
          >
            {query}
          </li>
        ))}
      </ul>
    );
  }

  if (item.state === 'count_only') {
    return (
      <div className="border-border-subtle bg-panel text-muted rounded-md border px-3 py-2 text-xs">
        {countOnlyExplanation(item)}
      </div>
    );
  }

  return (
    <div className="border-border-subtle bg-panel text-muted flex items-center gap-2 rounded-md border px-3 py-2.5 text-xs">
      <MinusCircle className="size-3.5 shrink-0" aria-hidden />
      <span>No web searches performed for this execution</span>
    </div>
  );
}
