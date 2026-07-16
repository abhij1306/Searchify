'use client';

import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

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
 * The project list comes from F2's `projects.ts`. If the backend endpoint is
 * not live yet it returns an empty list (or errors, handled by the guard's
 * global 401 watchdog), and we default to `/setup`.
 */
function LandingRedirect() {
  const router = useRouter();

  const { data: projects, isLoading } = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: ({ signal }) => projectsApi.listProjects({ signal }),
  });

  useEffect(() => {
    if (isLoading) return;
    router.replace(projects && projects.length > 0 ? '/visibility' : '/setup');
  }, [isLoading, projects, router]);

  return <LandingSplash />;
}

function LandingSplash() {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background p-6">
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
