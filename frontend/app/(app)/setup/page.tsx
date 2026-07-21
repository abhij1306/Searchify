'use client';

import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { Skeleton } from '@/components/ui/skeleton';
import { SetupForm } from '@/components/setup/setup-form';
import { useProjectContext } from '@/lib/project/project-context';

/**
 * `/setup` (F6) — the sidebar "Setup" destination.
 *
 * With an active project this is its **edit** form (redirect to
 * `/setup/[projectId]`), so returning users land on their brand details rather
 * than a blank create form. Without any project it renders the first-run
 * create form. Creating another project explicitly lives at `/setup/new`.
 */
export default function SetupPage() {
  const router = useRouter();
  const { activeProjectId, isLoading } = useProjectContext();

  useEffect(() => {
    if (activeProjectId) router.replace(`/setup/${activeProjectId}`);
  }, [activeProjectId, router]);

  // While projects load — or while the redirect above is in flight — show a
  // skeleton instead of flashing the empty create form.
  if (isLoading || activeProjectId) {
    return (
      <div className="mx-auto grid max-w-3xl gap-4" aria-hidden>
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <SetupForm />
    </div>
  );
}
