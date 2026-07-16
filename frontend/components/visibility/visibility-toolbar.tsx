'use client';

import { ChevronDown } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { Tooltip } from '@/components/ui/tooltip';
import type { LogicalEngine, Visibility } from '@/lib/api/types';
import {
  engineLabel,
  presentEngines,
  type RunOption,
  type VisibilityFilters,
} from '@/lib/visibility/dashboard';

/**
 * Dashboard header controls (design.md §9.6): a run selector (defaults to the
 * latest completed audit) plus an engine filter. Each active control updates
 * state that participates in the visibility query key, so changing it
 * re-derives the view. The prompt-type (Branded / Non-branded) filter is
 * DISABLED at MVP — the backend `VisibilityResponse` has no per-prompt-type
 * breakdown — and is surfaced as a "coming soon" affordance so the layout is
 * preserved. No date/run-range trend control (roadmap).
 */
export function VisibilityToolbar({
  runs,
  activeRunId,
  onSelectRun,
  filters,
  onChangeFilters,
  visibility,
}: Readonly<{
  runs: RunOption[];
  activeRunId: string | null;
  onSelectRun: (runId: string) => void;
  filters: VisibilityFilters;
  onChangeFilters: (filters: VisibilityFilters) => void;
  visibility: Visibility | undefined;
}>) {
  const activeRun = runs.find((run) => run.id === activeRunId) ?? null;
  const engines = presentEngines(visibility);
  const engineText = filters.engine === 'all' ? 'All engines' : engineLabel(filters.engine);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Dropdown>
        <DropdownTrigger asChild>
          <Button variant="secondary" size="sm" aria-label="Select run">
            <span className="text-muted">Run:</span>
            <span className="font-medium">{activeRun?.label ?? 'Latest'}</span>
            <ChevronDown className="size-4" aria-hidden />
          </Button>
        </DropdownTrigger>
        <DropdownContent>
          <DropdownLabel>Runs</DropdownLabel>
          {runs.map((run) => (
            <DropdownItem
              key={run.id}
              data-active={run.id === activeRunId}
              onSelect={() => onSelectRun(run.id)}
            >
              {run.label}
            </DropdownItem>
          ))}
        </DropdownContent>
      </Dropdown>

      <Dropdown>
        <DropdownTrigger asChild>
          <Button variant="secondary" size="sm" aria-label="Filter by engine">
            <span className="text-muted">Engine:</span>
            <span className="font-medium">{engineText}</span>
            <ChevronDown className="size-4" aria-hidden />
          </Button>
        </DropdownTrigger>
        <DropdownContent>
          <DropdownLabel>Engine</DropdownLabel>
          <DropdownItem
            data-active={filters.engine === 'all'}
            onSelect={() => onChangeFilters({ ...filters, engine: 'all' })}
          >
            All engines
          </DropdownItem>
          {engines.map((engine: LogicalEngine) => (
            <DropdownItem
              key={engine}
              data-active={filters.engine === engine}
              onSelect={() => onChangeFilters({ ...filters, engine })}
            >
              {engineLabel(engine)}
            </DropdownItem>
          ))}
        </DropdownContent>
      </Dropdown>

      <Tooltip content="Prompt-type filtering is coming soon.">
        <Button
          variant="secondary"
          size="sm"
          aria-label="Filter by prompt type"
          disabled
          aria-disabled
        >
          <span className="text-muted">Prompts:</span>
          <span className="font-medium">All prompts</span>
          <span className="ml-1 text-2xs text-muted">— coming soon</span>
          <ChevronDown className="size-4" aria-hidden />
        </Button>
      </Tooltip>
    </div>
  );
}
