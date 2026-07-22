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
import { AccentEyebrow } from '@/components/ui/eyebrow';
import { IconChip } from '@/components/ui/icon-chip';
import { displayHeadingLgClasses, displayHeadingXlClasses } from '@/components/ui/typography';

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
            <IconChip>
              <BookOpen className="size-6" aria-hidden />
            </IconChip>
            <div className="grid gap-1">
              <h2 className={displayHeadingLgClasses}>Create a project first</h2>
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
        <AccentEyebrow>Knowledge base</AccentEyebrow>
        <h2 className={displayHeadingXlClasses}>Brand knowledge</h2>
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
