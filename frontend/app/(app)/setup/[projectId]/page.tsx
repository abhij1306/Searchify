'use client';

import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { SetupForm } from '@/components/setup/setup-form';
import { projectsApi } from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import { setupErrorMessage } from '@/lib/setup/forms';

/**
 * `/setup/[projectId]` (F6) — edit an existing Brand-Project.
 *
 * Fetches the project via F2's `projects.ts`, prefills the shared `SetupForm`
 * wizard (edit mode), and PATCHes on save. The header names the project being
 * edited and offers "Add another project" (→ `/setup/new`), since `/setup`
 * itself always lands here once a project exists.
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
    <div className="mx-auto grid max-w-3xl gap-5">
      {isLoading ? (
        <div className="grid gap-4" aria-hidden>
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : error ? (
        <Alert tone="danger">{setupErrorMessage(error)}</Alert>
      ) : project ? (
        <>
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-foreground truncate text-lg font-semibold">
                {project.brand_name || project.name}
              </h2>
              <p className="text-secondary text-sm">
                Project set up — edit the details below, or start another brand.
              </p>
            </div>
            <Button variant="secondary" asChild>
              <Link href="/setup/new">
                <Plus className="size-4" aria-hidden />
                Add another project
              </Link>
            </Button>
          </div>
          <SetupForm project={project} />
        </>
      ) : null}
    </div>
  );
}
