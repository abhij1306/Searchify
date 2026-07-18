import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

/**
 * Selected-run dashboard loading placeholder, shared by the page's `<Suspense>`
 * boundary (required by `useSearchParams`) and the Overview tab's own loading
 * state so the two render identically.
 */
export function DashboardSkeleton() {
  return (
    <div className="grid gap-6" aria-hidden>
      <div className="grid gap-6 lg:grid-cols-[minmax(260px,1fr)_2fr]">
        <Card>
          <CardContent className="grid justify-items-center gap-4">
            <Skeleton className="size-28 rounded-full" />
            <Skeleton className="h-4 w-40" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="grid gap-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardContent className="grid gap-4 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32 w-full" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
