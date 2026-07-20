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
import type { LogicalEngine } from '@/lib/api/types';
import {
  engineLabel,
  isEvidenceTab,
  type PromptOption,
  type RunOption,
  type VisibilityTab,
} from '@/lib/visibility/dashboard';
import {
  GRANULARITY_OPTIONS,
  RANGE_OPTIONS,
  TREND_ENGINES,
  granularityLabel,
  rangeLabel,
  type TrendGranularity,
  type TrendRange,
} from '@/lib/visibility/trends';

/** Engine filter value shared across every tab. */
export type EngineFilter = LogicalEngine | 'all';

/**
 * Shared filter bar rendered ABOVE the tablist (design.md tabbed workspace).
 *
 * A single control row whose filter STATE lives in `visibility-dashboard.tsx`
 * and persists across tab switches. Only the controls relevant to the active
 * tab are shown; hidden controls keep their state and reappear unchanged. There
 * is no Single Run / Trend mode toggle — the tablist replaces it.
 *
 * Ownership (plan §IA):
 *   - Run:         Overview + both evidence tabs
 *   - Engine:      all four tabs
 *   - Prompt:      both evidence tabs (evidence filtering, NOT prompt taxonomy)
 *   - Range:       Trends + both evidence tabs
 *   - Granularity: Trends only
 *   - Prompt-type "coming soon": Overview only (the MVP taxonomy affordance)
 */
export function VisibilityToolbar({
  activeTab,
  runs,
  activeRunId,
  onSelectRun,
  engine,
  onChangeEngine,
  promptOptions,
  promptId,
  onChangePrompt,
  range,
  onChangeRange,
  granularity,
  onChangeGranularity,
}: Readonly<{
  activeTab: VisibilityTab;
  runs: RunOption[];
  activeRunId: string | null;
  onSelectRun: (runId: string) => void;
  engine: EngineFilter;
  onChangeEngine: (engine: EngineFilter) => void;
  promptOptions: PromptOption[];
  promptId: string | null;
  onChangePrompt: (promptId: string | null) => void;
  range: TrendRange;
  onChangeRange: (range: TrendRange) => void;
  granularity: TrendGranularity;
  onChangeGranularity: (granularity: TrendGranularity) => void;
}>) {
  const evidence = isEvidenceTab(activeTab);
  const showRun = activeTab === 'overview' || evidence;
  const showPrompt = evidence;
  const showRange = activeTab === 'trends' || evidence;
  const showGranularity = activeTab === 'trends';
  const showPromptType = activeTab === 'overview';

  const activeRun = runs.find((run) => run.id === activeRunId) ?? null;
  const engineText = engine === 'all' ? 'All engines' : engineLabel(engine);
  const activePrompt = promptOptions.find((option) => option.id === promptId) ?? null;
  const promptText = promptId === null ? 'All prompts' : (activePrompt?.label ?? 'All prompts');

  return (
    <div className="flex flex-wrap items-center gap-3" data-testid="visibility-toolbar">
      {showRun ? (
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
      ) : null}

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
          <DropdownItem data-active={engine === 'all'} onSelect={() => onChangeEngine('all')}>
            All engines
          </DropdownItem>
          {TREND_ENGINES.map((option: LogicalEngine) => (
            <DropdownItem
              key={option}
              data-active={engine === option}
              onSelect={() => onChangeEngine(option)}
            >
              {engineLabel(option)}
            </DropdownItem>
          ))}
        </DropdownContent>
      </Dropdown>

      {showRange ? (
        <Dropdown>
          <DropdownTrigger asChild>
            <Button variant="secondary" size="sm" aria-label="Select date range">
              <span className="text-muted">Range:</span>
              <span className="font-medium">{rangeLabel(range)}</span>
              <ChevronDown className="size-4" aria-hidden />
            </Button>
          </DropdownTrigger>
          <DropdownContent>
            <DropdownLabel>Date range</DropdownLabel>
            {RANGE_OPTIONS.map((option) => (
              <DropdownItem
                key={option.value}
                data-active={range === option.value}
                onSelect={() => onChangeRange(option.value)}
              >
                {option.label}
              </DropdownItem>
            ))}
          </DropdownContent>
        </Dropdown>
      ) : null}

      {showGranularity ? (
        <Dropdown>
          <DropdownTrigger asChild>
            <Button variant="secondary" size="sm" aria-label="Select granularity">
              <span className="text-muted">Granularity:</span>
              <span className="font-medium">{granularityLabel(granularity)}</span>
              <ChevronDown className="size-4" aria-hidden />
            </Button>
          </DropdownTrigger>
          <DropdownContent>
            <DropdownLabel>Granularity</DropdownLabel>
            {GRANULARITY_OPTIONS.map((option) => (
              <DropdownItem
                key={option.value}
                data-active={granularity === option.value}
                onSelect={() => onChangeGranularity(option.value)}
              >
                {option.label}
              </DropdownItem>
            ))}
          </DropdownContent>
        </Dropdown>
      ) : null}

      {showPrompt ? (
        <Dropdown>
          <DropdownTrigger asChild>
            <Button variant="secondary" size="sm" aria-label="Filter by prompt">
              <span className="text-muted">Prompt:</span>
              <span className="max-w-[16ch] truncate font-medium">{promptText}</span>
              <ChevronDown className="size-4" aria-hidden />
            </Button>
          </DropdownTrigger>
          <DropdownContent>
            <DropdownLabel>Prompt</DropdownLabel>
            <DropdownItem data-active={promptId === null} onSelect={() => onChangePrompt(null)}>
              All prompts
            </DropdownItem>
            {promptOptions.map((option) => (
              <DropdownItem
                key={option.id}
                data-active={promptId === option.id}
                onSelect={() => onChangePrompt(option.id)}
              >
                {option.label}
              </DropdownItem>
            ))}
          </DropdownContent>
        </Dropdown>
      ) : null}

      {showPromptType ? (
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
            <span className="text-2xs text-muted ml-1">— coming soon</span>
            <ChevronDown className="size-4" aria-hidden />
          </Button>
        </Tooltip>
      ) : null}
    </div>
  );
}
