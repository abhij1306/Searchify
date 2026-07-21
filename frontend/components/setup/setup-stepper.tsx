'use client';

import { Check } from 'lucide-react';

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
                  'flex size-8 shrink-0 items-center justify-center rounded-full border-2 text-sm font-semibold transition-colors',
                  isDone
                    ? 'border-accent bg-accent text-white'
                    : isCurrent
                      ? 'border-accent text-accent-text bg-accent-soft'
                      : 'border-border text-muted bg-panel',
                )}
              >
                {isDone ? <Check className="size-4" /> : index + 1}
              </span>
              <span
                className={cn(
                  'text-2xs max-w-20 text-center leading-tight font-medium whitespace-nowrap',
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
