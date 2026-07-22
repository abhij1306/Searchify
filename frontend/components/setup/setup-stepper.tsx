'use client';

import { Check } from 'lucide-react';

import { eyebrowClasses } from '@/components/ui/eyebrow';
import { cn } from '@/lib/utils';

export type SetupStep = {
  id: string;
  label: string;
};

/**
 * SetupStepper (F6) — the horizontal progress line for the setup wizard.
 *
 * Numbered circles joined by connector lines; completed steps show a check and
 * the connector fills with the accent color. Visited steps are clickable so
 * the user can move back (or jump forward over already-completed steps)
 * without losing entered values — react-hook-form keeps state across step
 * unmounts.
 *
 * CUBE27 midnight language (Phase D6): mono tabular numerals, an accent ring
 * halo on the active circle, accent-filled completed circles with a check,
 * and mono uppercase micro-labels under each circle.
 */
export function SetupStepper({
  steps,
  current,
  maxVisited,
  onSelect,
}: Readonly<{
  steps: readonly SetupStep[];
  current: number;
  maxVisited: number;
  onSelect: (index: number) => void;
}>) {
  return (
    <ol className="flex items-start" aria-label="Setup steps">
      {steps.map((step, index) => {
        const isCurrent = index === current;
        const isDone = index < current;
        const reachable = index <= maxVisited && !isCurrent;
        return (
          <li
            key={step.id}
            className={cn('flex items-start', index > 0 && 'flex-1')}
            aria-current={isCurrent ? 'step' : undefined}
          >
            {index > 0 ? (
              <div
                aria-hidden
                className={cn(
                  'mt-[15px] h-0.5 min-w-4 flex-1 rounded-full transition-colors',
                  isDone || isCurrent ? 'bg-accent' : 'bg-border',
                )}
              />
            ) : null}
            <button
              type="button"
              onClick={() => reachable && onSelect(index)}
              disabled={!reachable}
              className={cn(
                'group flex flex-col items-center gap-1.5 px-2',
                reachable ? 'cursor-pointer' : 'cursor-default',
              )}
            >
              <span
                aria-hidden
                className={cn(
                  'flex size-8 shrink-0 items-center justify-center rounded-full border-2 font-mono text-sm font-semibold tabular-nums transition-[background-color,border-color,color,box-shadow]',
                  isDone
                    ? 'border-accent bg-accent text-accent-fg'
                    : isCurrent
                      ? 'border-accent text-accent-text bg-accent-soft ring-accent-soft ring-4'
                      : 'border-border text-muted bg-panel',
                )}
              >
                {isDone ? <Check className="size-4" /> : index + 1}
              </span>
              <span
                className={cn(
                  eyebrowClasses,
                  'text-center leading-tight whitespace-nowrap',
                  isCurrent
                    ? 'text-foreground font-semibold'
                    : isDone
                      ? 'text-secondary'
                      : 'text-muted',
                  reachable && 'group-hover:text-foreground',
                )}
              >
                {step.label}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
