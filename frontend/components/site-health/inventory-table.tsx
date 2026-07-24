'use client';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { InventoryRow } from '@/lib/api/types';
import { PageTypeBadge } from '@/components/site-health/page-type-badge';

/** The cursor-paginated inventory rows with per-row monitored checkboxes. */
export function InventoryTable({
  rows,
  isStaged,
  disabled,
  onToggle,
}: Readonly<{
  rows: readonly InventoryRow[];
  isStaged: (siteUrlId: string) => boolean;
  disabled: boolean;
  onToggle: (siteUrlId: string) => void;
}>) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-10" />
          <TableHead>Page URL</TableHead>
          <TableHead>Page Type</TableHead>
          <TableHead>Content Type</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.site_url_id}>
            <TableCell>
              <input
                type="checkbox"
                checked={isStaged(row.site_url_id)}
                disabled={disabled}
                aria-label={`Monitor ${row.display_url}`}
                onChange={() => onToggle(row.site_url_id)}
                className="focus-ring accent-accent size-4 shrink-0"
              />
            </TableCell>
            <TableCell>
              <span className="flex flex-col">
                <span className="text-foreground font-medium">{row.title ?? row.display_url}</span>
                <span className="mono text-2xs text-muted">{row.display_url}</span>
              </span>
            </TableCell>
            <TableCell>
              <PageTypeBadge pageType={row.page_type} />
            </TableCell>
            <TableCell className="text-secondary text-xs">{row.content_type ?? '—'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
