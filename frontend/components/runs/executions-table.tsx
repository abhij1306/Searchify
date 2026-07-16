'use client';

import Link from 'next/link';

import { Badge } from '@/components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { engineLabel, transportLabel } from '@/lib/providers/catalog';
import type { Execution } from '@/lib/api/types';
import { executionBadgeValue, executionStatusLabel } from '@/lib/runs/status';

/**
 * Executions table for a run (F10, design.md §9.7).
 *
 * One row per execution/queue task: prompt index + repetition, the engine badge
 * (logical + transport), status badge, and latency (mono). Succeeded rows link
 * to the evidence page for that execution.
 */
export function ExecutionsTable({
  auditId,
  executions,
}: Readonly<{ auditId: string; executions: Execution[] }>) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Prompt</TableHead>
          <TableHead>Engine</TableHead>
          <TableHead>Status</TableHead>
          <TableHead numeric>Latency</TableHead>
          <TableHead className="sr-only">Evidence</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {executions.map((execution) => (
          <TableRow key={execution.id}>
            <TableCell>
              <span className="text-sm text-foreground">#{execution.prompt_index + 1}</span>
              <span className="ml-2 text-xs text-muted">rep {execution.repetition}</span>
            </TableCell>
            <TableCell>
              <span className="text-sm text-foreground">
                {engineLabel(execution.logical_engine)}
              </span>
              <span className="ml-1.5 text-xs text-muted">
                {transportLabel(execution.transport_provider)}
              </span>
            </TableCell>
            <TableCell>
              <Badge variant="status" value={executionBadgeValue(execution.status)}>
                {executionStatusLabel(execution.status)}
              </Badge>
            </TableCell>
            <TableCell numeric className="mono">
              {execution.latency_ms == null ? '—' : `${execution.latency_ms} ms`}
            </TableCell>
            <TableCell>
              {execution.status === 'succeeded' ? (
                <Link
                  href={`/runs/${auditId}/executions/${execution.id}`}
                  className="text-sm font-medium text-accent-text hover:underline"
                >
                  Evidence
                </Link>
              ) : (
                <span className="text-sm text-subtle">—</span>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
