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

import { TablePagination, useTablePage } from './table-pagination';

/** Rows per page on the runs table (client-side; the list arrives whole). */
const PAGE_SIZE = 10;

/**
 * Runs (audits) list table (F10, design.md §9.7).
 *
 * One row per audit: a run-status badge, the requested/completed/failed mono
 * counts, and the created timestamp. Each row links to the run detail page.
 * Client-side pagination footer (mono indicator + ghost buttons) per the
 * midnight runs frame.
 */
export function RunsTable({ audits }: Readonly<{ audits: Audit[] }>) {
  const { page, setPage, pageCount, from, to } = useTablePage(audits.length, PAGE_SIZE);
  const pagedAudits = audits.slice(from - 1, to);

  return (
    <div>
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
          {pagedAudits.map((audit) => (
            <TableRow key={audit.id}>
              <TableCell>
                <Badge variant="run-status" value={auditBadgeValue(audit.status)}>
                  {auditStatusLabel(audit.status)}
                </Badge>
              </TableCell>
              <TableCell numeric className="mono">
                {audit.requested_count}
              </TableCell>
              <TableCell numeric className="mono">
                {audit.completed_count}
              </TableCell>
              <TableCell numeric className="mono">
                {audit.failed_count}
              </TableCell>
              <TableCell className="text-secondary">{formatDateTime(audit.created_at)}</TableCell>
              <TableCell>
                <Link
                  href={`/runs/${audit.id}`}
                  className="text-accent-text text-sm font-medium hover:underline"
                >
                  View
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <TablePagination
        page={page}
        pageCount={pageCount}
        from={from}
        to={to}
        total={audits.length}
        noun="runs"
        onPageChange={setPage}
      />
    </div>
  );
}
