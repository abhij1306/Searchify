'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef } from 'react';

import { Skeleton } from '@/components/ui/skeleton';
import { SetupForm } from '@/components/setup/setup-form';
import { AccentEyebrow } from '@/components/ui/eyebrow';
import { displayHeadingXlClasses } from '@/components/ui/typography';
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

  // Whether an active project already existed when the projects query first
  // resolved. Distinguishes "arrived on /setup with a project" (redirect to
  // its edit form) from "just created one in the embedded form below": the
  // create mutation sets the active project *while this page is still
  // mounted* and then routes to `/prompts` itself — an unconditional redirect
  // here fires in the same window and hijacks that navigation, landing fresh
  // users on the edit form instead of `/prompts`.
  const hadProjectOnLoad = useRef<boolean | null>(null);

  useEffect(() => {
    if (isLoading) return;
    hadProjectOnLoad.current ??= Boolean(activeProjectId);
    if (activeProjectId && hadProjectOnLoad.current) {
      router.replace(`/setup/${activeProjectId}`);
    }
  }, [activeProjectId, isLoading, router]);

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
    <div className="mx-auto grid max-w-3xl gap-5">
      <div className="grid gap-1">
        <AccentEyebrow>New project</AccentEyebrow>
        <h2 className={displayHeadingXlClasses}>Set up your brand project</h2>
        <p className="text-secondary text-sm">
          Two short steps — your brand and its market. Domains, competitors, and audit defaults can
          be refined anytime after your first run.
        </p>
      </div>
      <SetupForm />
    </div>
  );
}
