'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';

import { Alert } from '@/components/ui/alert';
import { buttonVariants } from '@/components/ui/button-variants';
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
      <Alert tone="info">
        Create a project before adding brand knowledge.{' '}
        <Link href="/setup" className={buttonVariants({ variant: 'secondary', size: 'sm' })}>
          Go to Setup
        </Link>
      </Alert>
    );
  }

  if (profileQuery.isLoading) return <Skeleton className="h-96 w-full" />;
  if (profileQuery.error) {
    return <Alert tone="danger">{setupErrorMessage(profileQuery.error)}</Alert>;
  }
  if (!profileQuery.data) return null;

  return (
    <div className="mx-auto grid max-w-3xl gap-4">
      <p className="text-secondary text-sm">
        Maintain the facts and positioning that Searchify uses to ground assisted features.
      </p>
      <BrandProfilePanel
        key={profileQuery.data.updated_at}
        projectId={project.id}
        profile={profileQuery.data}
      />
    </div>
  );
}
