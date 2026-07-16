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
import type { LogicalEngine, Visibility } from '@/lib/api/types';
import {
  PROMPT_TYPE_OPTIONS,
  engineLabel,
  presentEngines,
  type PromptTypeFilter,
  type RunOption,
  type VisibilityFilters,
} from '@/lib/visibility/dashboard';

/**
 * Dashboard header controls (design.md §9.6): a run selector (defaults to the
 * latest completed audit) plus engine and prompt-type filters. Each control
 * updates state that participates in the visibility query key, so changing a
 * selection re-derives the view. No date/run-range trend control (roadmap).
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
  const promptText =
    PROMPT_TYPE_OPTIONS.find((option) => option.value === filters.promptType)?.label ?? 'All prompts';

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

      <Dropdown>
        <DropdownTrigger asChild>
          <Button variant="secondary" size="sm" aria-label="Filter by prompt type">
            <span className="text-muted">Prompts:</span>
            <span className="font-medium">{promptText}</span>
            <ChevronDown className="size-4" aria-hidden />
          </Button>
        </DropdownTrigger>
        <DropdownContent>
          <DropdownLabel>Prompt type</DropdownLabel>
          {PROMPT_TYPE_OPTIONS.map((option) => (
            <DropdownItem
              key={option.value}
              data-active={filters.promptType === option.value}
              onSelect={() =>
                onChangeFilters({ ...filters, promptType: option.value as PromptTypeFilter })
              }
            >
              {option.label}
            </DropdownItem>
          ))}
        </DropdownContent>
      </Dropdown>
    </div>
  );
}
