'use client';

import { useParams } from 'next/navigation';
import { Suspense } from 'react';

import { Skeleton } from '@/components/ui/skeleton';
import { UrlDetail } from '@/components/site-health/url-detail';

/**
 * Per-URL Site Health detail (Slice 8, mockup 711).
 *
 * Route: `/site-health/crawls/[crawlId]/pages/[siteUrlId]`. Renders URL
 * metadata, overall/Technical/AEO scores, persisted delivery facts, current
 * issues ordered by severity, and crawl-bounded issue history. This is the
 * destination the dashboard "View" actions and the Issues catalog affected-URL
 * links navigate to.
 *
 * `<UrlDetail>` reads `useSearchParams`, so it sits under `<Suspense>` to keep
 * the rest of the page statically renderable. The fallback mirrors UrlDetail's
 * own query-loading skeleton so suspension and data-loading look identical.
 */
export default function UrlDetailPage() {
  const params = useParams<{ crawlId: string; siteUrlId: string }>();
  return (
    <Suspense
      fallback={
        <div className="grid gap-6" aria-hidden>
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      }
    >
      <UrlDetail crawlId={params.crawlId} siteUrlId={params.siteUrlId} />
    </Suspense>
  );
}
