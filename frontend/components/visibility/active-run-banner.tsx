import Link from 'next/link';
import { LoaderCircle } from 'lucide-react';

import { Alert } from '@/components/ui/alert';
import { auditStatusLabel } from '@/lib/runs/status';
import type { ActiveRun } from '@/lib/visibility/dashboard';

/**
 * In-progress run banner for the Visibility workspace. An active run has no
 * metric snapshot yet, so it can't appear in the run selector — this banner is
 * how the user learns a run is underway (and where to watch it) instead of
 * seeing stale data or a bare empty state after navigating back mid-run.
 */
export function ActiveRunBanner({ run }: Readonly<{ run: ActiveRun }>) {
  return (
    <Alert tone="info" hideIcon>
      <div className="flex flex-wrap items-center gap-2">
        <LoaderCircle className="size-4 shrink-0 animate-spin" aria-hidden />
        <span>
          A run is in progress ({auditStatusLabel(run.status)}). Results appear here when it
          completes.
        </span>
        <Link
          href={`/runs/${run.id}`}
          className="text-accent-text font-medium whitespace-nowrap hover:underline"
        >
          Watch live progress →
        </Link>
      </div>
    </Alert>
  );
}
