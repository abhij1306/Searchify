/**
 * Authenticated Site Health export download (Slice 7).
 *
 * Exports must be authenticated blob downloads, NOT a plain `<a href>` /
 * `window.location` navigation. A bare navigation cannot carry the active
 * workspace's `X-Workspace-Id` header, so a selected non-default workspace's
 * export would silently resolve against the caller's *default* workspace (or a
 * different crawl). `apiClient.getBlob` stamps `X-Workspace-Id` + credentials
 * exactly like every other request, so the download is scoped to the workspace
 * the user is actually looking at.
 *
 * The blob is materialised into a temporary object URL, clicked programmatically,
 * and then revoked — no leaked object URL, no navigation away from the app.
 */
import { apiClient, type ApiRequestOptions } from '@/lib/api/client';
import { siteHealthApi } from '@/lib/api/site-health';

/** A Site Health export view. Markdown always exports the inventory view. */
export type ExportView = 'inventory' | 'pages' | 'issues';
export type ExportFormat = 'csv' | 'md';

/** Build a stable download filename for a crawl export. */
export function exportFilename(
  crawlId: string,
  format: ExportFormat,
  view: ExportView,
): string {
  const shortId = crawlId.slice(0, 8);
  return format === 'csv'
    ? `site-health-${view}-${shortId}.csv`
    : `site-health-${shortId}.md`;
}

/**
 * Download a Site Health export as an authenticated blob. Fetches the bytes via
 * `apiClient.getBlob` (which carries `X-Workspace-Id` + credentials), triggers a
 * browser download through a short-lived object URL, and revokes it afterwards.
 * Returns nothing; throws `ApiError` on a non-2xx response so the caller can
 * surface a message.
 */
export async function downloadCrawlExport(
  crawlId: string,
  format: ExportFormat,
  view: ExportView = 'inventory',
  options?: ApiRequestOptions,
): Promise<void> {
  // The same-origin export path (relative to `/api/v1`) that `exportUrl`
  // produces; strip the API base so `apiClient` re-adds it.
  const absolutePath = siteHealthApi.exportUrl(crawlId, format, view);
  const path = absolutePath.replace(/^\/api\/v1/, '');
  const blob = await apiClient.getBlob(path, options);
  saveBlob(blob, exportFilename(crawlId, format, view));
}

/**
 * Persist a blob to the user's downloads via a temporary object URL. SSR-safe:
 * a no-op when there is no `document`. The object URL is always revoked so it
 * does not leak (memory + a live blob reference).
 */
export function saveBlob(blob: Blob, filename: string): void {
  if (typeof document === 'undefined' || typeof URL.createObjectURL !== 'function') {
    return;
  }
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
