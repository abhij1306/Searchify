'use client';

import { useParams } from 'next/navigation';

import { UrlDetail } from '@/components/site-health/url-detail';

/**
 * Per-URL Site Health detail (Slice 8, mockup 711).
 *
 * Route: `/site-health/crawls/[crawlId]/pages/[siteUrlId]`. Renders URL
 * metadata, overall/Technical/AEO scores, persisted delivery facts, current
 * issues ordered by severity, and crawl-bounded issue history. This is the
 * destination the dashboard "View" actions and the Issues catalog affected-URL
 * links navigate to.
 */
export default function UrlDetailPage() {
  const params = useParams<{ crawlId: string; siteUrlId: string }>();
  return <UrlDetail crawlId={params.crawlId} siteUrlId={params.siteUrlId} />;
}
