/**
 * TanStack Query client factory + retry policy (F2).
 *
 * `shouldRetryQuery`: retry transient failures only — network errors and
 * 408/429/5xx — capped at 2 attempts. 4xx (except 408/429) and aborts never
 * retry. `staleTime` 15s and `refetchOnWindowFocus:false` match the reference
 * frontend. Mutations never auto-retry.
 */
import { QueryClient } from '@tanstack/react-query';

import { httpErrorStatus, isAbortError } from './errors';

export function shouldRetryQuery(failureCount: number, error: unknown) {
  if (failureCount >= 2 || isAbortError(error)) return false;
  const status = httpErrorStatus(error);
  if (status === undefined) return true; // network / unknown → retry
  return status === 408 || status === 429 || status >= 500;
}

export function createAppQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetryQuery,
        staleTime: 15_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
