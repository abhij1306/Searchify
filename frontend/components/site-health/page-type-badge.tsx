'use client';

import { Badge } from '@/components/ui/badge';
import { pageTypeLabel } from '@/lib/site-health/page-types';
import { PLACEHOLDER } from '@/lib/site-health/status';

/**
 * The page-type chip (site-health v2 P1) rendered on page rows (pages +
 * inventory), affected-URL rows, and the per-URL detail header. Reuses the
 * design-system neutral `Badge` — no new colour family. An unclassified page
 * (no completed analysis yet, or a projection that does not carry the field)
 * renders the `—` placeholder, never a guessed type.
 */
export function PageTypeBadge({ pageType }: Readonly<{ pageType: string | null | undefined }>) {
  if (!pageType) {
    return <span className="text-muted">{PLACEHOLDER}</span>;
  }
  return <Badge>{pageTypeLabel(pageType)}</Badge>;
}
