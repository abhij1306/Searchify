'use client';

import { Filter, Search, Upload } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dropdown,
  DropdownCheckboxItem,
  DropdownContent,
  DropdownLabel,
  DropdownSeparator,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { Input } from '@/components/ui/input';
import { intentLabels, intentValues } from '@/lib/prompts/forms';
import type { EnabledFilter, PromptFilters } from '@/lib/prompts/filter';

/**
 * Prompt library toolbar (F7): search box, an intent + enabled filter menu, a
 * CSV bulk-upload button, and the primary "Add prompt" action. Presentational —
 * state lives in the page.
 */
export function PromptToolbar({
  search,
  onSearchChange,
  filters,
  onFiltersChange,
  onImport,
  onAdd,
  disabled,
}: Readonly<{
  search: string;
  onSearchChange: (value: string) => void;
  filters: PromptFilters;
  onFiltersChange: (filters: PromptFilters) => void;
  onImport: () => void;
  onAdd: () => void;
  disabled?: boolean;
}>) {
  const activeFilterCount =
    filters.intents.length +
    (filters.enabled === 'all' ? 0 : 1) +
    (filters.branded === 'all' ? 0 : 1);

  // Set lookup: `checked` is computed per intent option in the render loop.
  const selectedIntents = new Set(filters.intents);

  const toggleIntent = (value: string) => {
    const next = selectedIntents.has(value)
      ? filters.intents.filter((intent) => intent !== value)
      : [...filters.intents, value];
    onFiltersChange({ ...filters, intents: next });
  };

  const setEnabled = (value: EnabledFilter) => onFiltersChange({ ...filters, enabled: value });
  const setBranded = (value: EnabledFilter) => onFiltersChange({ ...filters, branded: value });

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-[220px] flex-1">
        <Search
          className="text-muted pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2"
          aria-hidden
        />
        <Input
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search prompts"
          aria-label="Search prompts"
          className="pl-8"
        />
      </div>

      <Dropdown>
        <DropdownTrigger asChild>
          <Button variant="secondary">
            <Filter className="size-4" aria-hidden />
            Filter
            {activeFilterCount > 0 ? (
              <span className="bg-accent-subtle text-2xs text-accent-text ml-1 rounded-full px-1.5 font-mono font-semibold">
                {activeFilterCount}
              </span>
            ) : null}
          </Button>
        </DropdownTrigger>
        <DropdownContent align="end" className="w-56">
          <DropdownLabel>Intent</DropdownLabel>
          {intentValues.map((value) => (
            <DropdownCheckboxItem
              key={value || 'unspecified'}
              checked={selectedIntents.has(value)}
              onSelect={(event) => {
                event.preventDefault();
                toggleIntent(value);
              }}
            >
              {intentLabels[value]}
            </DropdownCheckboxItem>
          ))}
          <DropdownSeparator className="bg-border-subtle my-1 h-px" />
          <DropdownLabel>Enabled</DropdownLabel>
          {(['all', 'enabled', 'disabled'] as const).map((value) => (
            <DropdownCheckboxItem
              key={value}
              checked={filters.enabled === value}
              onSelect={(event) => {
                event.preventDefault();
                setEnabled(value);
              }}
            >
              {value === 'all' ? 'All' : value === 'enabled' ? 'Enabled only' : 'Disabled only'}
            </DropdownCheckboxItem>
          ))}
          <DropdownSeparator className="bg-border-subtle my-1 h-px" />
          <DropdownLabel>Branded</DropdownLabel>
          {(['all', 'enabled', 'disabled'] as const).map((value) => (
            <DropdownCheckboxItem
              key={value}
              checked={filters.branded === value}
              onSelect={(event) => {
                event.preventDefault();
                setBranded(value);
              }}
            >
              {value === 'all' ? 'All' : value === 'enabled' ? 'Branded only' : 'Unbranded only'}
            </DropdownCheckboxItem>
          ))}
        </DropdownContent>
      </Dropdown>

      <Button variant="secondary" onClick={onImport} disabled={disabled}>
        <Upload className="size-4" aria-hidden />
        Bulk upload
      </Button>
      <Button variant="primary" onClick={onAdd} disabled={disabled}>
        Add prompt
      </Button>
    </div>
  );
}
