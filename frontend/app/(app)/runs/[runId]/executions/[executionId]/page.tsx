'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useParams } from 'next/navigation';

import { Alert } from '@/components/ui/alert';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { EvidenceCard } from '@/components/runs/evidence-card';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return 'Something went wrong. Please try again.';
}

/**
 * Execution evidence screen (F10, design.md §9.7).
 *
 * Loads one execution's persisted evidence via `GET /executions/{id}` (keyed by
 * the execution/task id from the run's executions table) and renders the answer,
 * grounding, classified citations, mentions, and per-response score.
 */
export default function ExecutionEvidencePage() {
  const params = useParams<{ runId: string; executionId: string }>();
  const { runId, executionId } = params;

  const evidenceQuery = useQuery({
    queryKey: queryKeys.runs.execution(executionId),
    queryFn: ({ signal }) => runsApi.getExecution(executionId, { signal }),
  });

  // The answer text lives on the execution/queue row (AuditTaskResponse), not the
  // projection-only evidence endpoint; both share the execution id space, so we
  // resolve it from the run's executions list.
  const executionsQuery = useQuery({
    queryKey: queryKeys.runs.executions(runId),
    queryFn: ({ signal }) => runsApi.listExecutions(runId, { signal }),
  });
  const answerText =
    executionsQuery.data?.find((row) => row.id === executionId)?.answer_text ?? null;

  return (
    <div className="grid gap-6">
      <div>
        <Link
          href={`/runs/${runId}`}
          className="text-accent-text text-xs font-medium hover:underline"
        >
          ← Back to run
        </Link>
      </div>

      {evidenceQuery.isError ? (
        <Alert tone="danger">
          Could not load this execution&apos;s evidence. {errorMessage(evidenceQuery.error)}
        </Alert>
      ) : evidenceQuery.isLoading || !evidenceQuery.data ? (
        <Card>
          <CardContent className="grid gap-3">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-24 w-full" />
          </CardContent>
        </Card>
      ) : (
        <EvidenceCard evidence={evidenceQuery.data} answerText={answerText} />
      )}
    </div>
  );
}
