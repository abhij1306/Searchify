'use client';

import Link from 'next/link';
import { Check } from 'lucide-react';

import { useProjectContext } from '@/lib/project/project-context';
import { cn } from '@/lib/utils';

/**
 * GettingStartedCard (F5) — the "Getting Started N of 6" onboarding progress
 * card in the sidebar (docs/design.md §9.2).
 *
 * The six MVP onboarding steps map to the live surfaces a user completes to get
 * their first run. Completion is derived from what the active project already
 * has (project created → prompts added → provider connected …). At MVP we can
 * only *cheaply* infer the first two from the loaded project; the rest link to
 * their screens and are shown as not-yet-complete. It is intentionally a light
 * nudge, not a source of truth.
 */
type Step = { label: string; href: string; done: boolean };

export function GettingStartedCard({ className }: Readonly<{ className?: string }>) {
  const { activeProject } = useProjectContext();

  const hasProject = Boolean(activeProject);
  const hasPrompts = (activeProject?.prompt_sets ?? []).some((set) => set.prompts.length > 0);

  const steps: Step[] = [
    { label: 'Create your project', href: '/setup', done: hasProject },
    { label: 'Add brand details', href: '/setup', done: hasProject },
    { label: 'Add prompts', href: '/prompts', done: hasPrompts },
    { label: 'Connect a provider', href: '/providers', done: false },
    { label: 'Launch your first run', href: '/runs', done: false },
    { label: 'Review visibility', href: '/visibility', done: false },
  ];

  const completed = steps.filter((step) => step.done).length;
  const total = steps.length;
  const nextStep = steps.find((step) => !step.done) ?? steps[steps.length - 1];
  const pct = Math.round((completed / total) * 100);

  return (
    <section
      className={cn('border-border bg-panel shadow-card rounded-lg border p-3', className)}
      aria-label="Getting started progress"
    >
      <div className="flex items-center justify-between">
        <span className="text-2xs text-muted font-semibold tracking-wide uppercase">
          Getting Started
        </span>
        <span className="text-2xs text-secondary font-semibold">
          {completed} of {total}
        </span>
      </div>

      <div
        className="bg-neutral-bg mt-2 h-1.5 w-full overflow-hidden rounded-full"
        role="progressbar"
        aria-valuenow={completed}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label={`${completed} of ${total} steps complete`}
      >
        <div
          className="bg-accent h-full rounded-full transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>

      <Link
        href={nextStep.href}
        className="text-accent-text mt-2.5 flex items-center gap-2 text-sm font-medium hover:underline"
      >
        {completed === total ? (
          <>
            <Check className="size-4 shrink-0" aria-hidden />
            <span>All set — you&apos;re ready to run</span>
          </>
        ) : (
          <span className="truncate">Next: {nextStep.label}</span>
        )}
      </Link>
    </section>
  );
}
