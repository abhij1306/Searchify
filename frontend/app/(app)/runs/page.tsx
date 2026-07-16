'use client';

import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PageTitle } from '@/components/ui/typography';
import { LaunchDialog } from '@/components/runs/launch-dialog';
import { RunsTable } from '@/components/runs/runs-table';
import { queryKeys } from '@/lib/api/query-keys';
import { runsApi } from '@/lib/api/runs';
import { useActiveProject } from '@/lib/project/project-context';

/**
 * Runs list screen (F10, design.md §9.7).
 *
 * Lists the active project's audits (status + requested/completed/failed counts
 * + created timestamp) with a "Launch audit" button opening the launch dialog.
 * On a successful launch it routes into the new run's detail page. Scoped to the
 * active project via F5 context.
 */
export default function RunsPage() {
  const project = useActiveProject();
  const projectId = project?.id ?? null;
  const router = useRouter();
  const [launchOpen, setLaunchOpen] = useState(false);

  const runsQuery = useQuery({
    queryKey: queryKeys.runs.list({ project_id: projectId ?? '' }),
    queryFn: ({ signal }) => runsApi.listAudits({ project_id: projectId as string }, { signal }),
    enabled: Boolean(projectId),
  });

  const audits = runsQuery.data ?? [];

  return (
    <div className="grid gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <PageTitle kicker="Runs">Audits</PageTitle>
          <p className="mt-1 max-w-2xl text-sm text-secondary">
            Each run asks your prompts across the selected AI engines and scores your brand&apos;s
            visibility. Launch a run, then open it to watch progress and inspect the evidence.
          </p>
        </div>
        <Button onClick={() => setLaunchOpen(true)} disabled={!projectId}>
          Launch audit
        </Button>
      </div>

      {!projectId ? (
        <Alert tone="info">Select or create a project to launch runs.</Alert>
      ) : runsQuery.isError ? (
        <Alert tone="danger">Could not load runs. Check your connection and try again.</Alert>
      ) : runsQuery.isLoading ? (
        <Card>
          <CardContent className="grid gap-3">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </CardContent>
        </Card>
      ) : audits.length === 0 ? (
        <Card>
          <CardContent className="grid gap-3 py-10 text-center">
            <p className="text-base font-semibold text-foreground">No runs yet</p>
            <p className="mx-auto max-w-md text-sm text-secondary">
              Launch your first audit to measure how AI engines answer questions about your brand.
            </p>
            <div className="mt-1 flex justify-center">
              <Button onClick={() => setLaunchOpen(true)}>Launch your first audit</Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="p-0">
            <RunsTable audits={audits} />
          </CardContent>
        </Card>
      )}

      {projectId ? (
        <LaunchDialog
          open={launchOpen}
          onOpenChange={setLaunchOpen}
          projectId={projectId}
          onLaunched={(audit) => router.push(`/runs/${audit.id}`)}
        />
      ) : null}
    </div>
  );
}
