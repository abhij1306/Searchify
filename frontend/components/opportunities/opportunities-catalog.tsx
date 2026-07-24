'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';

import { Alert } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { CursorPager } from '@/components/ui/cursor-pager';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
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
import {
  EvidenceDrawer,
  OpportunityStatusBadge,
  OpportunityTypeBadge,
} from '@/components/opportunities/evidence-drawer';
import {
  opportunitiesMutations,
  opportunitiesQueries,
  type OpportunitiesParams,
} from '@/lib/api/opportunities';
import { queryKeys } from '@/lib/api/query-keys';
import type { Opportunity, OpportunityStatus } from '@/lib/api/types';
import { severityBadgeValue, severityLabel } from '@/lib/site-health/issues';
import { formatAudited } from '@/lib/site-health/status';
import { cn } from '@/lib/utils';

const PAGE_LIMIT = 25;

/**
 * Opportunities catalog (approved mockup: dense priority table + side drawer).
 *
 * Server-backed severity/type/status filter chips (never a client-side filter
 * over the current page), the priority-sorted keyset table (ordering is
 * server-owned: priority desc), a per-row status dropdown (the one mutation),
 * and row-click drill-down into the evidence drawer.
 */

type TypeFilter = 'all' | 'visibility' | 'site' | 'topic';
type SeverityFilter = 'all' | 'high' | 'medium' | 'low';
type StatusFilter = 'active' | OpportunityStatus;

const TYPE_FILTERS: ReadonlyArray<{ key: TypeFilter; label: string }> = [
  { key: 'all', label: 'All types' },
  { key: 'visibility', label: 'Visibility' },
  { key: 'site', label: 'Site' },
  { key: 'topic', label: 'Topic' },
];

const SEVERITY_FILTERS: ReadonlyArray<{ key: SeverityFilter; label: string }> = [
  { key: 'all', label: 'All severities' },
  { key: 'high', label: 'High' },
  { key: 'medium', label: 'Medium' },
  { key: 'low', label: 'Low' },
];

// The server's no-status-param default IS the active triage queue
// (open + in_progress), so the honest chip label is "Active".
const STATUS_FILTERS: ReadonlyArray<{ key: StatusFilter; label: string }> = [
  { key: 'active', label: 'Active' },
  { key: 'open', label: 'Open' },
  { key: 'in_progress', label: 'In progress' },
  { key: 'dismissed', label: 'Dismissed' },
  { key: 'resolved', label: 'Resolved' },
];

const STATUS_CHOICES: ReadonlyArray<{ value: OpportunityStatus; label: string }> = [
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: 'resolved', label: 'Resolved' },
];

function FilterChip({
  label,
  selected,
  onSelect,
}: Readonly<{ label: string; selected: boolean; onSelect: () => void }>) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'focus-ring rounded-full border px-3 py-1 text-xs font-medium transition-colors',
        selected
          ? 'border-accent-border bg-accent-subtle text-accent-text'
          : 'border-border bg-panel text-secondary hover:border-border-strong hover:text-foreground',
      )}
    >
      {label}
    </button>
  );
}

/** The sub-title target line: URL for site rows, theme / target key otherwise. */
function targetLine(row: Opportunity): string {
  if (row.target_url) return row.target_url;
  if (row.target_theme) return `theme: ${row.target_theme}`;
  return row.target_key;
}

/** Priority cell: mono score + a token-colored bar (capped at 100 for width). */
function PriorityCell({ score }: Readonly<{ score: number }>) {
  const width = Math.max(2, Math.min(100, score));
  return (
    <div className="grid min-w-24 gap-1">
      <span className="mono text-foreground text-sm font-semibold">{score.toFixed(1)}</span>
      <span className="bg-border-subtle block h-1 w-24 rounded-full">
        <span className="bg-accent block h-1 rounded-full" style={{ width: `${width}%` }} />
      </span>
    </div>
  );
}

/** Per-row status control (dropdown → updateStatus mutation). */
function StatusControl({
  row,
  projectId,
}: Readonly<{ row: Opportunity; projectId: string }>) {
  const queryClient = useQueryClient();
  const updateStatus = useMutation({
    ...opportunitiesMutations.updateStatus(),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.list(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.summary(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.opportunities.detail(row.id),
        }),
      ]);
    },
  });
  return (
    <Dropdown>
      <DropdownTrigger asChild>
        <button
          type="button"
          aria-label={`Change status for ${row.title}`}
          className="focus-ring rounded-full"
          // Row click opens the drawer — the status control must not.
          onClick={(event) => event.stopPropagation()}
        >
          <OpportunityStatusBadge status={row.status} />
        </button>
      </DropdownTrigger>
      <DropdownContent>
        {STATUS_CHOICES.map((choice) => (
          <DropdownItem
            key={choice.value}
            disabled={choice.value === row.status || updateStatus.isPending}
            onSelect={() =>
              updateStatus.mutate({ opportunityId: row.id, status: choice.value })
            }
          >
            {choice.label}
          </DropdownItem>
        ))}
      </DropdownContent>
    </Dropdown>
  );
}

export function OpportunitiesCatalog({ projectId }: Readonly<{ projectId: string }>) {
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('active');
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const cursor = cursorStack.at(-1);
  const params: OpportunitiesParams = useMemo(
    () => ({
      type: typeFilter === 'all' ? undefined : typeFilter,
      severity: severityFilter === 'all' ? undefined : severityFilter,
      status: statusFilter === 'active' ? undefined : statusFilter,
      cursor,
      limit: PAGE_LIMIT,
    }),
    [typeFilter, severityFilter, statusFilter, cursor],
  );

  const listQuery = useQuery(opportunitiesQueries.list(projectId, params));
  const rows = listQuery.data?.items ?? [];
  const nextCursor = listQuery.data?.next_cursor ?? null;
  const canPrev = cursorStack.length > 0;

  const resetPages = () => setCursorStack([]);
  const goNext = () => {
    if (nextCursor) setCursorStack((prev) => [...prev, nextCursor]);
  };
  const goPrev = () => setCursorStack((prev) => prev.slice(0, -1));

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {TYPE_FILTERS.map((filter) => (
            <FilterChip
              key={filter.key}
              label={filter.label}
              selected={filter.key === typeFilter}
              onSelect={() => {
                setTypeFilter(filter.key);
                resetPages();
              }}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {SEVERITY_FILTERS.map((filter) => (
            <FilterChip
              key={filter.key}
              label={filter.label}
              selected={filter.key === severityFilter}
              onSelect={() => {
                setSeverityFilter(filter.key);
                resetPages();
              }}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {STATUS_FILTERS.map((filter) => (
            <FilterChip
              key={filter.key}
              label={filter.label}
              selected={filter.key === statusFilter}
              onSelect={() => {
                setStatusFilter(filter.key);
                resetPages();
              }}
            />
          ))}
        </div>
      </div>

      {listQuery.isError ? (
        <Alert tone="danger">Could not load opportunities. Please refresh.</Alert>
      ) : listQuery.isLoading ? (
        <div className="grid gap-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <Card>
          <CardContent className="text-secondary text-sm">
            No opportunities match this view.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Priority</TableHead>
                <TableHead>Opportunity</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Detected</TableHead>
                <TableHead className="w-8" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  className="hover:bg-background-alt cursor-pointer"
                  onClick={() => setSelectedId(row.id)}
                >
                  <TableCell>
                    <PriorityCell score={row.priority_score} />
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-0.5">
                      <span className="text-foreground text-sm font-medium">{row.title}</span>
                      <span className="mono text-2xs text-muted break-all">
                        {targetLine(row)}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <OpportunityTypeBadge type={row.opportunity_type} />
                  </TableCell>
                  <TableCell>
                    <Badge variant="status" value={severityBadgeValue(row.severity)}>
                      {severityLabel(row.severity)}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <StatusControl row={row} projectId={projectId} />
                  </TableCell>
                  <TableCell>
                    <span className="text-secondary text-xs whitespace-nowrap">
                      {formatAudited(row.created_at)}
                    </span>
                  </TableCell>
                  <TableCell>
                    <ChevronRight className="text-muted size-4" aria-hidden />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      {rows.length > 0 ? (
        <div className="flex items-center justify-end gap-2">
          <CursorPager
            canPrev={canPrev}
            canNext={Boolean(nextCursor)}
            onPrev={goPrev}
            onNext={goNext}
          />
        </div>
      ) : null}

      <EvidenceDrawer
        opportunityId={selectedId}
        projectId={projectId}
        open={selectedId !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
      />
    </div>
  );
}
