'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  type ReactNode,
} from 'react';

import { authApi } from '@/lib/api/auth';
import { httpErrorStatus } from '@/lib/api/errors';
import { queryKeys } from '@/lib/api/query-keys';
import type { SessionUser } from '@/lib/api/types';

type SessionContextValue = {
  user: SessionUser;
  /** Clear all cached session state and send the user back to `/login`. */
  clearSession: () => void;
};

const SessionContext = createContext<SessionContextValue | null>(null);

/**
 * SessionGuard (F4) — the authed-area gate + user context provider.
 *
 * Mounted at the top of the `(app)` route group. It:
 *   1. loads `GET /auth/me` (React Query; 4xx never retries per F2's policy);
 *   2. redirects to `/login` whenever there is no authenticated user;
 *   3. installs a QueryCache listener so a 401 from ANY query (not just `me`)
 *      clears the cached session and redirects to `/login` (invariant: a cookie
 *      that expired mid-session must not strand the user on a broken screen).
 *
 * While `me` is loading, it renders `fallback` (a neutral splash) rather than
 * flashing protected content.
 */
export function SessionGuard({
  children,
  fallback = null,
}: Readonly<{ children: ReactNode; fallback?: ReactNode }>) {
  const router = useRouter();
  const queryClient = useQueryClient();

  const {
    data: user,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: queryKeys.auth.me(),
    queryFn: ({ signal }) => authApi.me({ signal }),
  });

  const clearSession = useCallback(() => {
    queryClient.clear();
    router.replace('/login');
  }, [queryClient, router]);

  // Redirect any unauthenticated visitor (failed `me`) to the login screen.
  useEffect(() => {
    if (isError) clearSession();
  }, [isError, clearSession]);

  // Global 401 watchdog: a 401 from any in-flight/finished query means the
  // session is gone — clear + redirect once, regardless of which query failed.
  useEffect(() => {
    const cache = queryClient.getQueryCache();
    const unsubscribe = cache.subscribe((event) => {
      if (event.type !== 'updated') return;
      const queryError = event.query.state.error;
      if (queryError && httpErrorStatus(queryError) === 401) {
        clearSession();
      }
    });
    return unsubscribe;
  }, [queryClient, clearSession]);

  const value = useMemo<SessionContextValue | null>(
    () => (user ? { user, clearSession } : null),
    [user, clearSession],
  );

  if (isLoading || !value) {
    // Loading, or unauthenticated and mid-redirect: never render protected UI.
    // Surface the underlying error only for debugging (kept out of the DOM).
    void error;
    return <>{fallback}</>;
  }

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** Access the authenticated session user. Throws if used outside the guard. */
export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) {
    throw new Error('useSession must be used within a <SessionGuard>.');
  }
  return context;
}

/** Convenience accessor for just the user record. */
export function useSessionUser(): SessionUser {
  return useSession().user;
}
