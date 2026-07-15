'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

/**
 * QueryProvider — TanStack Query v5 client provider (F1 stub).
 *
 * F1 ships a minimal provider so the app tree has a query context and
 * `next build` succeeds. F2 replaces the inline client with the shared
 * `lib/api/query-client.ts` factory (retry policy: 408/429/5xx/network up to
 * 2x, staleTime 15s, refetchOnWindowFocus:false).
 */
export function QueryProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
