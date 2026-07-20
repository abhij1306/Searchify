'use client';

import { useQuery } from '@tanstack/react-query';
import { useParams } from 'next/navigation';

import { Alert } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton';
import { SetupForm } from '@/components/setup/setup-form';
import { projectsApi } from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import { setupErrorMessage } from '@/lib/setup/forms';

/**
 * `/setup/[projectId]` (F6) — edit an existing Brand-Project.
 *
 * Fetches the project via F2's `projects.ts`, prefills the shared `SetupForm`
 * (edit mode), and PATCHes on save.
 */
export default function EditSetupPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;

  const {
    data: project,
    isLoading,
    error,
  } = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: ({ signal }) => projectsApi.getProject(projectId, { signal }),
    enabled: Boolean(projectId),
  });

  return (
    <div className="mx-auto max-w-3xl">
      {isLoading ? (
        <div className="grid gap-4" aria-hidden>
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : error ? (
        <Alert tone="danger">{setupErrorMessage(error)}</Alert>
      ) : project ? (
        <SetupForm project={project} />
      ) : null}
    </div>
  );
}
