'use client';

/**
 * Credentialed Site Health crawl-event stream (Slice 7).
 *
 * SSE is ONLY an invalidation accelerator — polling (in the screen) is the
 * reliable baseline. When a `crawl_updated` / page / event arrives on the
 * stream we invalidate the relevant Site Health queries so rows move
 * queued → running → completed/error/blocked without waiting for the next poll
 * tick. A dropped, timed-out, or disconnected stream MUST NOT stop progress:
 * this hook never surfaces a fatal error and the screen keeps polling.
 *
 * We use an abortable credentialed `fetch` + `ReadableStream` reader rather than
 * the native `EventSource`, because `EventSource` cannot send the
 * `X-Workspace-Id` header the backend needs to scope a non-default workspace's
 * stream (it only issues a bare same-origin GET). `apiClient` is JSON-only, so
 * this is the one place we call `fetch` directly — with the same credentials +
 * workspace header contract.
 */
import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

import { API_BASE_URL, getActiveWorkspaceId } from '@/lib/api/client';
import { queryKeys } from '@/lib/api/query-keys';

/**
 * Subscribe to a crawl's SSE event stream while `enabled`. On every parsed
 * event (or any received data) we invalidate the crawl + pages + dashboard +
 * inventory + issues queries for that crawl so the UI re-fetches promptly. All
 * failures are swallowed — polling remains the source of progress.
 */
export function useCrawlEvents(
  crawlId: string | null | undefined,
  projectId: string | null | undefined,
  enabled: boolean,
): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled || !crawlId) return;

    const controller = new AbortController();
    let cancelled = false;

    const invalidate = () => {
      // Move page rows through their lifecycle and refresh the crawl summary +
      // dashboard scores. Invalidate ALL page/inventory/issue queries for this
      // crawl (every filter/cursor combination), never just one client page.
      queryClient.invalidateQueries({ queryKey: queryKeys.siteHealth.crawl(crawlId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.siteHealth.pages(crawlId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.siteHealth.inventory(crawlId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.siteHealth.issues(crawlId) });
      if (projectId) {
        queryClient.invalidateQueries({
          queryKey: queryKeys.siteHealth.dashboard(projectId),
        });
      }
    };

    const run = async () => {
      try {
        const headers: Record<string, string> = { Accept: 'text/event-stream' };
        const workspaceId = getActiveWorkspaceId();
        if (workspaceId) headers['X-Workspace-Id'] = workspaceId;

        const response = await fetch(`${API_BASE_URL}/site-crawls/${crawlId}/events?stream=true`, {
          method: 'GET',
          headers,
          credentials: 'include',
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!response.ok || !response.body) return;

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        // Read frames until the stream ends (terminal grace / timeout) or the
        // effect is torn down. Every complete SSE frame triggers an invalidation.
        for (;;) {
          const { value, done } = await reader.read();
          if (done || cancelled) break;
          buffer += decoder.decode(value, { stream: true });
          // SSE frames are separated by a blank line.
          let sep = buffer.indexOf('\n\n');
          while (sep !== -1) {
            const frame = buffer.slice(0, sep);
            buffer = buffer.slice(sep + 2);
            // Ignore keep-alive comments (":" prefix); invalidate on any data.
            if (frame.split('\n').some((line) => line.startsWith('data:'))) {
              invalidate();
            }
            sep = buffer.indexOf('\n\n');
          }
        }
      } catch {
        // Dropped / aborted / timed-out streams are non-fatal: polling in the
        // screen continues to advance progress. Swallow silently.
      }
    };

    void run();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [crawlId, projectId, enabled, queryClient]);
}
