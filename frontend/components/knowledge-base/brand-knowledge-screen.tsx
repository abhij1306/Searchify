'use client';

import { useQuery } from '@tanstack/react-query';
import { BookOpen } from 'lucide-react';
import Link from 'next/link';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardEyebrow } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { projectsApi } from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import { useActiveProject } from '@/lib/project/project-context';
import { setupErrorMessage } from '@/lib/setup/forms';

import { BrandProfilePanel } from './brand-profile-panel';

/**
 * Workspace-owned editor for the curated brand knowledge used by assisted
 * features. Project setup deliberately owns only identity and audit defaults;
 * this screen owns the richer, separately-maintained knowledge profile.
 *
 * Midnight composition: accent-dot eyebrow + display heading over the editor
 * card; the no-project state is the standard eyebrow + display-heading +
 * ghost-CTA empty card.
 */
export function BrandKnowledgeScreen() {
  const project = useActiveProject();
  const projectId = project?.id;
  const profileQuery = useQuery({
    queryKey: projectId
      ? queryKeys.projects.brandProfile(projectId)
      : ['projects', 'brand-profile', 'none'],
    queryFn: ({ signal }) => projectsApi.getBrandProfile(projectId as string, { signal }),
    enabled: Boolean(projectId),
  });

  if (!project) {
    return (
      <div className="mx-auto grid max-w-3xl">
        <Card>
          <CardContent className="grid justify-items-center gap-4 py-12 text-center">
            <CardEyebrow>Knowledge base</CardEyebrow>
            <span className="bg-accent-subtle text-accent-text flex size-12 items-center justify-center rounded-full">
              <BookOpen className="size-6" aria-hidden />
            </span>
            <div className="grid gap-1">
              <h2 className="font-display text-foreground text-lg font-semibold">
                Create a project first
              </h2>
              <p className="text-secondary max-w-md text-sm">
                Brand knowledge belongs to a project. Set one up, then curate the facts and
                positioning Searchify uses to ground assisted features.
              </p>
            </div>
            <Button asChild variant="ghost" size="md">
              <Link href="/setup">Go to Setup</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (profileQuery.isLoading) {
    return (
      <div className="mx-auto grid max-w-3xl gap-5" aria-hidden>
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (profileQuery.error) {
    return (
      <div className="mx-auto grid max-w-3xl">
        <Alert tone="danger">{setupErrorMessage(profileQuery.error)}</Alert>
      </div>
    );
  }
  if (!profileQuery.data) return null;

  return (
    <div className="mx-auto grid max-w-3xl gap-5">
      <div className="grid gap-1">
        <span className="text-accent-text text-2xs inline-flex items-center gap-1.5 font-mono font-medium tracking-[0.08em] uppercase">
          <span className="bg-accent size-1.5 rounded-full" aria-hidden />
          Knowledge base
        </span>
        <h2 className="font-display text-foreground text-xl font-semibold">Brand knowledge</h2>
        <p className="text-secondary text-sm">
          Maintain the facts and positioning that Searchify uses to ground assisted features.
        </p>
      </div>
      <BrandProfilePanel
        key={profileQuery.data.updated_at}
        projectId={project.id}
        profile={profileQuery.data}
      />
    </div>
  );
}
