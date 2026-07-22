'use client';

import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Midnight filter chip (designs/shell-runs-midnight.html `.chipf`): a pill
 * button with a hairline border; the active chip takes the accent-soft fill +
 * accent text (blue stays reserved for active states). Shared, as class
 * builder + pressed-button, by the runs status filter chips (aria-pressed)
 * and the launch dialog's engine chips (role=checkbox).
 */
export function filterChipClasses(active: boolean): string {
  return cn(
    'focus-ring inline-flex h-[var(--control-height-sm)] items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-[background-color,color,border-color]',
    active
      ? 'border-accent-border bg-accent-soft text-accent-text'
      : 'border-border bg-panel text-secondary hover:border-border-strong hover:text-foreground',
  );
}

export function FilterChip({
  active,
  onClick,
  count,
  children,
}: Readonly<{
  active: boolean;
  onClick: () => void;
  /** Optional mono count rendered after the label (muted, tabular). */
  count?: number;
  children: ReactNode;
}>) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={filterChipClasses(active)}
    >
      {children}
      {typeof count === 'number' ? (
        <span className={cn('mono text-2xs', active ? 'text-accent-text' : 'text-muted')}>
          {count}
        </span>
      ) : null}
    </button>
  );
}
