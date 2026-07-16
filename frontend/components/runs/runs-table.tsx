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
import type { Audit } from '@/lib/api/types';
import { auditBadgeValue, auditStatusLabel, formatDateTime } from '@/lib/runs/status';

/**
 * Runs (audits) list table (F10, design.md §9.7).
 *
 * One row per audit: a run-status badge, the requested/completed/failed mono
 * counts, and the created timestamp. Each row links to the run detail page.
 */
export function RunsTable({ audits }: Readonly<{ audits: Audit[] }>) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead numeric>Requested</TableHead>
          <TableHead numeric>Completed</TableHead>
          <TableHead numeric>Failed</TableHead>
          <TableHead>Created</TableHead>
          <TableHead className="sr-only">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {audits.map((audit) => (
          <TableRow key={audit.id}>
            <TableCell>
              <Badge variant="run-status" value={auditBadgeValue(audit.status)}>
                {auditStatusLabel(audit.status)}
              </Badge>
            </TableCell>
            <TableCell numeric>{audit.requested_count}</TableCell>
            <TableCell numeric>{audit.completed_count}</TableCell>
            <TableCell numeric>{audit.failed_count}</TableCell>
            <TableCell className="text-secondary">{formatDateTime(audit.created_at)}</TableCell>
            <TableCell>
              <Link
                href={`/runs/${audit.id}`}
                className="text-sm font-medium text-accent-text hover:underline"
              >
                View
              </Link>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
