'use client';

import { QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { createAppQueryClient } from '@/lib/api/query-client';

/**
 * QueryProvider — TanStack Query v5 client provider.
 *
 * Uses the shared `lib/api/query-client.ts` factory so the retry policy
 * (`shouldRetryQuery`: 408/429/5xx/network up to 2×), `staleTime` 15s, and
 * `refetchOnWindowFocus:false` are defined in exactly one place. The client is
 * created once per mount via `useState` so it is stable across re-renders and
 * never shared across requests during SSR.
 */
export function QueryProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [client] = useState(() => createAppQueryClient());

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
