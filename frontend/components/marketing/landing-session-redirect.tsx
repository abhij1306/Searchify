'use client';

import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { authApi } from '@/lib/api/auth';
import { queryKeys } from '@/lib/api/query-keys';

/**
 * LandingSessionRedirect — client wrapper for the public marketing landing page (`/`).
 *
 * Checks `authApi.me` to prime the session query cache without forcing a redirect,
 * allowing signed-in visitors to view the landing page with the signed-in LandingNav.
 */
export function LandingSessionRedirect({ children }: Readonly<{ children?: ReactNode }>) {
  useQuery({
    queryKey: queryKeys.auth.me(),
    queryFn: ({ signal }) => authApi.me({ signal }),
    retry: false,
    refetchOnWindowFocus: false,
  });

  return <>{children}</>;
}
