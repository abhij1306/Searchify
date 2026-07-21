'use client';

import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Check } from 'lucide-react';

import { providersApi } from '@/lib/api/providers';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import { useProjectContext } from '@/lib/project/project-context';
import { cn } from '@/lib/utils';

/**
 * GettingStartedCard (F5) — the "Getting Started N of 6" onboarding progress
 * card in the sidebar (docs/design.md §9.2).
 *
 * The six MVP onboarding steps map to the live surfaces a user completes to get
 * their first run. Completion is fully derived from live data: project +
 * prompts from the loaded project, provider from the BYOK connections list
 * (an active connection with a stored key), and run/review from the project's
 * audits (any launched → step 5; any completed → step 6). It is intentionally
 * a light nudge, not a source of truth.
 */
type Step = { label: string; href: string; done: boolean };

export function GettingStartedCard({ className }: Readonly<{ className?: string }>) {
  const { activeProject } = useProjectContext();

  const hasProject = Boolean(activeProject);
  const hasPrompts = (activeProject?.prompt_sets ?? []).some((set) => set.prompts.length > 0);

  // Provider step: any active connection with a stored key counts. Only fetch
  // once a project exists (the earlier steps gate everything anyway) and stop
  // refetching once the card is fully complete.
  const connectionsQuery = useQuery({
    queryKey: queryKeys.providers.connections(),
    queryFn: ({ signal }) => providersApi.listConnections({ signal }),
    enabled: hasProject,
  });
  const hasProvider = (connectionsQuery.data ?? []).some(
    (connection) => connection.active && connection.api_key_set !== false,
  );

  // Run steps: share the runs list cache with /runs (same key + params).
  const projectId = activeProject?.id ?? '';
  const auditsQuery = useQuery({
    queryKey: queryKeys.runs.list({ project_id: projectId }),
    queryFn: ({ signal }) => runsApi.listAudits({ project_id: projectId }, { signal }),
    enabled: hasProject,
  });
  const audits = auditsQuery.data ?? [];
  const hasRun = audits.length > 0;
  const hasCompletedRun = audits.some(
    (audit) => audit.status === 'completed' || audit.status === 'partially_completed',
  );

  const steps: Step[] = [
    { label: 'Create your project', href: '/setup', done: hasProject },
    { label: 'Add brand details', href: '/setup', done: hasProject },
    { label: 'Add prompts', href: '/prompts', done: hasPrompts },
    { label: 'Connect a provider', href: '/settings?tab=providers', done: hasProvider },
    { label: 'Launch your first run', href: '/runs', done: hasRun },
    { label: 'Review visibility', href: '/visibility', done: hasCompletedRun },
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
