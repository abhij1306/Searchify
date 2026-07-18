'use client';

import type { UseQueryResult } from '@tanstack/react-query';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  EvidenceEmpty,
  EvidenceError,
  EvidenceFilteredEmpty,
  EvidenceSkeleton,
  TruncationNotice,
} from '@/components/visibility/evidence-states';
import { classificationBadgeValue, classificationLabel } from '@/lib/runs/status';
import type { VisibilityEvidenceResponse, VisibilityExecutionEvidence } from '@/lib/api/types';
import { engineLabel } from '@/lib/providers/catalog';
import {
  formatExecutionDate,
  provenanceSummary,
  shortId,
  totalCitationCount,
  totalMentionCount,
} from '@/lib/visibility/evidence';

const TITLE = 'Mentions & Citations';

/**
 * Mentions & Citations tab — persisted brand/competitor mention rows and
 * classified citation records, grouped by execution, with selected-run / prompt
 * / engine context and task/analysis/artifact provenance. It renders only
 * PERSISTED rows (never inferred) and does NOT render a generated-query list —
 * that belongs to Query Fanout.
 *
 * States: skeleton, retryable error, empty (no persisted evidence), filtered
 * empty, and a truncation notice when the newest window overflowed.
 */
export function MentionsCitations({
  query,
  isFiltered,
  onClearFilters,
  limit,
}: Readonly<{
  query: UseQueryResult<VisibilityEvidenceResponse, unknown>;
  isFiltered: boolean;
  onClearFilters?: () => void;
  limit: number;
}>) {
  if (query.isLoading) {
    return <EvidenceSkeleton title={TITLE} />;
  }
  if (query.isError) {
    return <EvidenceError title={TITLE} onRetry={() => query.refetch()} />;
  }

  const items = query.data?.items ?? [];
  const truncated = query.data?.truncated ?? false;

  // Only the executions that actually carry persisted mention/citation rows.
  const withEvidence = items.filter(
    (item) => item.mentions.length > 0 || item.citations.length > 0,
  );

  if (withEvidence.length === 0) {
    return isFiltered ? (
      <EvidenceFilteredEmpty
        title={TITLE}
        body="No persisted mentions or citations match the selected run, engine, prompt, and date range. Widen the range or clear a filter."
        onClear={onClearFilters}
      />
    ) : (
      <EvidenceEmpty
        title={TITLE}
        heading="No mentions or citations yet"
        body="Once a run executes your prompts, the brand and competitor mentions and the sources cited in each answer appear here."
      />
    );
  }

  const mentionCount = totalMentionCount(withEvidence);
  const citationCount = totalCitationCount(withEvidence);

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="grid gap-1">
          <CardTitle>{TITLE}</CardTitle>
          <p className="text-sm text-secondary">
            Persisted mentions and classified citations, grouped by execution.
          </p>
        </div>
        <Badge variant="neutral">
          {mentionCount} mentions · {citationCount} citations
        </Badge>
      </CardHeader>
      <CardContent className="grid gap-4 p-0">
        <ul className="grid gap-4 p-[var(--card-padding)]">
          {withEvidence.map((item) => (
            <ExecutionEvidenceRow key={item.analysis_id} item={item} />
          ))}
        </ul>
        {truncated ? <TruncationNotice limit={limit} /> : null}
      </CardContent>
    </Card>
  );
}

function ExecutionEvidenceRow({
  item,
}: Readonly<{ item: VisibilityExecutionEvidence }>) {
  return (
    <li className="grid gap-3 rounded-md border border-border-subtle bg-background-alt p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">
            {item.prompt_text || 'Untitled prompt'}
          </p>
          <p className="text-xs text-muted">
            Execution #{item.prompt_index} · rep {item.repetition} ·{' '}
            {formatExecutionDate(item.completed_at)}
          </p>
        </div>
        <span className="flex items-center gap-2">
          <Badge variant="neutral">{engineLabel(item.logical_engine)}</Badge>
          <span className="text-xs text-muted">{item.transport_model}</span>
        </span>
      </div>

      <p className="text-2xs uppercase tracking-wide text-muted">{provenanceSummary(item)}</p>

      {item.mentions.length > 0 ? (
        <div className="grid gap-1.5">
          <p className="text-2xs font-semibold uppercase tracking-wide text-muted">Mentions</p>
          <div className="flex flex-wrap gap-1.5">
            {item.mentions.map((mention) => (
              <Badge
                key={`${mention.kind}-${mention.name}-${mention.first_offset ?? 'na'}`}
                variant="classification"
                value={mention.kind === 'brand' ? 'owned' : 'competitor'}
              >
                {mention.name || (mention.kind === 'brand' ? 'Brand' : 'Competitor')}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}

      {item.citations.length > 0 ? (
        <div className="grid gap-1.5">
          <p className="text-2xs font-semibold uppercase tracking-wide text-muted">Citations</p>
          <ul className="grid gap-2">
            {item.citations.map((citation) => (
              <li
                key={`${item.analysis_id}-${citation.ordinal}-${citation.url}`}
                className="flex items-center justify-between gap-3 rounded-sm border border-border-subtle bg-panel px-3 py-2"
              >
                <span className="min-w-0 truncate text-xs text-secondary">
                  {citation.title?.trim() || citation.domain || citation.url}
                </span>
                <Badge
                  variant="classification"
                  value={classificationBadgeValue(citation.classification)}
                >
                  {classificationLabel(citation.classification)}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {item.artifact_id ? (
        <p className="text-2xs text-subtle">Artifact {shortId(item.artifact_id)}</p>
      ) : null}
    </li>
  );
}
