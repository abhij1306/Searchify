'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';

import { queryKeys } from '@/lib/api/query-keys';
import type { SessionUser } from '@/lib/api/types';

/**
 * Shared login/register mutation wiring (F4): on success, prime the `me` cache
 * with the returned user and redirect to the authed landing (`/`), which routes
 * on to `/visibility` or `/setup`. The submit handler swallows the rejection —
 * the error surfaces via `mutation.isError` in the page's inline alert.
 */
export function useAuthMutation<TValues>(mutationFn: (values: TValues) => Promise<SessionUser>) {
  const router = useRouter();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn,
    onSuccess: (user: SessionUser) => {
      queryClient.setQueryData(queryKeys.auth.me(), user);
      router.replace('/');
    },
  });

  const submit = (values: TValues) => mutation.mutateAsync(values).catch(() => undefined);

  return { mutation, submit };
}
