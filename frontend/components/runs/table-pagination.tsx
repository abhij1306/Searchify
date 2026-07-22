'use client';

import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';

/**
 * Midnight table pagination (designs/shell-runs-midnight.html): a mono
 * "from–to of total" page indicator plus ghost Prev/Next buttons, pinned to
 * the table card's bottom border. Local to the runs/prompts tables (a sibling
 * lives in components/prompts) — a candidate for promotion into
 * components/ui once a third table paginates.
 *
 * `useTablePage` owns the page state with clamp-only reconciliation: when the
 * underlying list shrinks (filters, deletes, polling refetches) the rendered
 * page clamps into range instead of resetting, so a 3s poll never yanks the
 * user back to page 1.
 */
export function useTablePage(total: number, pageSize: number) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pageCount);
  const from = total === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const to = Math.min(total, safePage * pageSize);
  return { page: safePage, setPage, pageCount, from, to };
}

export function TablePagination({
  page,
  pageCount,
  from,
  to,
  total,
  noun,
  onPageChange,
}: Readonly<{
  page: number;
  pageCount: number;
  from: number;
  to: number;
  total: number;
  /** Row noun for the indicator, e.g. "runs" / "prompts". */
  noun: string;
  onPageChange: (page: number) => void;
}>) {
  return (
    <div className="border-border-subtle flex items-center justify-between gap-3 border-t px-3 py-2">
      <span className="mono text-muted text-2xs">
        {from}–{to} of {total} {noun}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          aria-label="Previous page"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft className="size-3.5" aria-hidden />
          Prev
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Next page"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          Next
          <ChevronRight className="size-3.5" aria-hidden />
        </Button>
      </div>
    </div>
  );
}
