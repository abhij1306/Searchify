'use client';

import { useId, useRef, useState } from 'react';

import { Input } from '@/components/ui/input';
import type { MarketOption } from '@/lib/setup/markets';
import { cn } from '@/lib/utils';

/**
 * MarketSelect (F6) — a lightweight searchable select (combobox) for the
 * guided setup's Market step: an Input whose focus/typing opens a filtered
 * option list. Click or ArrowUp/ArrowDown + Enter to pick; blur commits a
 * typed text that exactly matches an option and otherwise reverts to the
 * selected label; Escape always reverts.
 *
 * Built on the standard Input + midnight dropdown tokens (bg-elevated,
 * shadow-elevated) rather than the Radix menu so typing focus never leaves
 * the input. Selection is committed via `onChange(value)`; the raw text is
 * component-local, so react-hook-form only ever sees valid option values.
 */
export function MarketSelect({
  id,
  ariaLabel,
  value,
  onChange,
  onBlur,
  options,
  placeholder,
  'aria-describedby': ariaDescribedBy,
  'aria-invalid': ariaInvalid,
  'aria-required': ariaRequired,
}: Readonly<{
  id?: string;
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  options: readonly MarketOption[];
  placeholder?: string;
  'aria-describedby'?: string;
  'aria-invalid'?: boolean;
  'aria-required'?: boolean;
}>) {
  const listId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  // `query` is null when the input shows the committed selection's label.
  const [query, setQuery] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(0);

  const selected = options.find((option) => option.value === value);
  // Off-list stored values (a project saved with a market outside the curated
  // list) still render their raw code rather than a misleading blank field.
  const text = query ?? selected?.label ?? value;
  const needle = (query ?? '').trim().toLowerCase();
  const filtered = needle
    ? options.filter(
        (option) =>
          option.label.toLowerCase().includes(needle) || option.value.toLowerCase().includes(needle),
      )
    : options;
  const showList = open && filtered.length > 0;

  const commit = (option: MarketOption) => {
    onChange(option.value);
    setQuery(null);
    setOpen(false);
  };

  const close = ({ commitExact = false }: { commitExact?: boolean } = {}) => {
    // On blur, a typed text that exactly matches an option (label or code) is
    // a completed selection — commit it rather than silently discarding it.
    // Escape passes commitExact: false and always reverts.
    if (commitExact && query !== null) {
      const q = query.trim().toLowerCase();
      const exact = options.find(
        (option) => option.label.toLowerCase() === q || option.value.toLowerCase() === q,
      );
      if (exact) {
        commit(exact);
        onBlur?.();
        return;
      }
    }
    setOpen(false);
    setQuery(null);
    onBlur?.();
  };

  return (
    <div ref={containerRef} className="relative">
      <Input
        id={id}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList ? `${listId}-${filtered[highlight]?.value}` : undefined}
        aria-label={ariaLabel}
        aria-describedby={ariaDescribedBy}
        aria-invalid={ariaInvalid}
        aria-required={ariaRequired}
        autoComplete="off"
        placeholder={placeholder}
        value={text}
        onFocus={() => {
          setOpen(true);
          setQuery('');
          setHighlight(0);
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setHighlight(0);
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown') {
            event.preventDefault();
            setOpen(true);
            setHighlight((current) => Math.min(current + 1, filtered.length - 1));
          } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setHighlight((current) => Math.max(current - 1, 0));
          } else if (event.key === 'Enter') {
            event.preventDefault();
            const option = filtered[highlight] ?? filtered[0];
            if (open && option) commit(option);
          } else if (event.key === 'Escape') {
            close();
          }
        }}
        onBlur={() => {
          // Defer so an option mousedown (which preventDefaults and keeps
          // focus) wins over the close.
          setTimeout(() => {
            if (!containerRef.current?.contains(document.activeElement)) {
              close({ commitExact: true });
            }
          }, 0);
        }}
      />
      {showList ? (
        <ul
          id={listId}
          role="listbox"
          aria-label={ariaLabel}
          className="border-border bg-elevated shadow-elevated absolute z-[300] mt-1 max-h-56 w-full overflow-auto rounded-md border p-1"
        >
          {filtered.map((option, index) => (
            <li
              key={option.value}
              id={`${listId}-${option.value}`}
              role="option"
              aria-selected={option.value === value}
              onMouseDown={(event) => {
                event.preventDefault();
                commit(option);
              }}
              onMouseEnter={() => setHighlight(index)}
              className={cn(
                'text-foreground flex cursor-pointer items-center justify-between gap-2 rounded-sm px-2.5 py-1.5 text-sm',
                index === highlight && 'bg-background-alt',
              )}
            >
              <span>{option.label}</span>
              <span className="text-2xs text-muted font-mono">{option.value}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
