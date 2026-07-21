'use client';

import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { authApi } from '@/lib/api/auth';
import { projectsApi } from '@/lib/api/projects';
import { queryKeys } from '@/lib/api/query-keys';

/**
 * LandingSessionRedirect — client island on the public marketing page (`/`).
 *
 * Preserves the old authed-landing routing contract now that `/` is public:
 *   - signed-in visitor with ≥1 project → `/visibility` (the live dashboard);
 *   - signed-in visitor with no project yet → `/setup` (first-run creation);
 *   - everyone else stays on the marketing page.
 *
 * The island renders `null` and never gates page content — no splash, no
 * children. It fires one `GET /auth/me` per visit (no retry, no window-focus
 * refetch: an anonymous visitor produces a single silent 401) and only then,
 * on success, the projects query. Post login/register the `me` cache is
 * already primed (`use-auth-mutation`), so the redirect resolves off cache.
 *
 * Failure matrix (deliberately inert): any `me` error and any projects error
 * leave the visitor where they are — an authed user with projects must never
 * be dumped into first-run `/setup` because one request failed.
 */
export function LandingSessionRedirect() {
  const router = useRouter();

  const me = useQuery({
    queryKey: queryKeys.auth.me(),
    queryFn: ({ signal }) => authApi.me({ signal }),
    retry: false,
    refetchOnWindowFocus: false,
  });

  const { data: projects, isSuccess: projectsLoaded } = useQuery({
    queryKey: queryKeys.projects.list(),
    queryFn: ({ signal }) => projectsApi.listProjects({ signal }),
    enabled: me.isSuccess,
  });

  useEffect(() => {
    if (projectsLoaded) {
      router.replace(projects.length > 0 ? '/visibility' : '/setup');
    }
  }, [projectsLoaded, projects, router]);

  return null;
}
