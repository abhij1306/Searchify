'use client';

import { useQuery } from '@tanstack/react-query';
import { redirect } from 'next/navigation';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { projectsApi } from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';
import { SessionGuard } from '@/lib/auth/session-guard';

/**
 * Authed landing (`/`) — F4.
 *
 * Wrapped in `<SessionGuard>` so an unauthenticated visitor is bounced to
 * `/login`. Once authenticated it inspects the project list and routes on:
 *   - has ≥1 project → `/visibility` (the live dashboard);
 *   - no project yet → `/setup` (first-run brand/project creation).
 *
 * The project list comes from F2's `projects.ts`. A 401 is handled by the
 * guard's global watchdog (→ /login). Any other failure (network blip, 5xx)
 * renders a retry state instead of redirecting — a user who has projects must
 * not be dumped into first-run setup because one request failed.
 *
 * The redirect is issued during render (not in an effect) via `redirect()`,
 * so the splash never flashes an already-resolved destination.
 */
function LandingRedirect() {
  const {
    data: projects,
    isSuccess,
    isError,
    refetch,
  } = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: ({ signal }) => projectsApi.listProjects({ signal }),
  });

  if (isError) {
    return (
      <main className="bg-background flex min-h-dvh items-center justify-center p-6">
        <div className="grid w-full max-w-md gap-3">
          <Alert tone="danger">Could not load your workspace. Please try again.</Alert>
          <Button variant="secondary" onClick={() => refetch()}>
            Retry
          </Button>
        </div>
      </main>
    );
  }

  if (isSuccess) {
    redirect(projects.length > 0 ? '/visibility' : '/setup');
  }

  return <LandingSplash />;
}

function LandingSplash() {
  return (
    <main className="bg-background flex min-h-dvh items-center justify-center p-6">
      <div className="grid w-full max-w-[280px] gap-3" aria-hidden>
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
      <span className="sr-only">Loading your workspace…</span>
    </main>
  );
}

export default function HomePage() {
  return (
    <SessionGuard fallback={<LandingSplash />}>
      <LandingRedirect />
    </SessionGuard>
  );
}
