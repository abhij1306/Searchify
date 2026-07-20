'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  CONTENT_DETAIL_POLL_MS,
  CONTENT_LIST_DEFAULT_LIMIT,
  CONTENT_LIST_POLL_MS,
  contentApi,
} from '@/lib/api/content';
import { queryKeys } from '@/lib/api/query-keys';
import type { ContentGenerationDetail, ContentGenerationStatus } from '@/lib/api/types';

const TERMINAL_STATUSES: ReadonlySet<ContentGenerationStatus> = new Set([
  'succeeded',
  'failed',
  'cancelled',
]);

export function isTerminalContentStatus(status: ContentGenerationStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** RFC 4122 v4 idempotency key: `randomUUID` when available, else built from
 * `getRandomValues` — the enqueue key must never be empty (the backend keys
 * replay-safety on it). */
function newIdempotencyKey(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID();
  const bytes = cryptoObj.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * Data orchestration for the Content screen.
 *
 * Progress is POLLING-ONLY (no SSE): the history list refetches at
 * `CONTENT_LIST_POLL_MS` while any visible item is non-terminal and stops
 * (`false`) once all are terminal; the selected detail refetches at
 * `CONTENT_DETAIL_POLL_MS` while the record is non-terminal (like `runs.ts`).
 * Every mutation invalidates the list; enqueue-like mutations also select the
 * new record so the screen follows it.
 */
export function useContentGenerations(
  projectId: string | null,
  limit: number = CONTENT_LIST_DEFAULT_LIMIT,
) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: queryKeys.content.list(projectId ?? '', limit),
    queryFn: ({ signal }) => contentApi.listGenerations(projectId ?? '', limit, { signal }),
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const items = query.state.data;
      if (!items || items.length === 0) return false;
      return items.some((item) => !isTerminalContentStatus(item.status))
        ? CONTENT_LIST_POLL_MS
        : false;
    },
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.content.detail(selectedId ?? ''),
    queryFn: ({ signal }) => contentApi.getGeneration(selectedId ?? '', { signal }),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => {
      const record = query.state.data;
      if (!record) return CONTENT_DETAIL_POLL_MS;
      return isTerminalContentStatus(record.status) ? false : CONTENT_DETAIL_POLL_MS;
    },
  });

  const invalidateList = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.content.all });
  };

  const followRecord = (record: ContentGenerationDetail) => {
    queryClient.setQueryData(queryKeys.content.detail(record.id), record);
    setSelectedId(record.id);
    invalidateList();
  };

  const enqueueMutation = useMutation({
    mutationFn: (input: { prompt: string; websiteContextEnabled: boolean }) =>
      contentApi.enqueueGeneration(
        {
          project_id: projectId ?? '',
          prompt: input.prompt,
          website_context_enabled: input.websiteContextEnabled,
        },
        newIdempotencyKey(),
      ),
    onSuccess: followRecord,
  });

  const regenerateMutation = useMutation({
    mutationFn: (generationId: string) => contentApi.regenerateGeneration(generationId),
    onSuccess: followRecord,
  });

  const tryAgainMutation = useMutation({
    mutationFn: (generationId: string) => contentApi.tryAgainGeneration(generationId),
    onSuccess: followRecord,
  });

  const cancelMutation = useMutation({
    mutationFn: (generationId: string) => contentApi.cancelGeneration(generationId),
    onSuccess: (record) => {
      queryClient.setQueryData(queryKeys.content.detail(record.id), record);
      invalidateList();
    },
  });

  return {
    listQuery,
    detailQuery,
    selectedId,
    setSelectedId,
    enqueueMutation,
    regenerateMutation,
    tryAgainMutation,
    cancelMutation,
  };
}
