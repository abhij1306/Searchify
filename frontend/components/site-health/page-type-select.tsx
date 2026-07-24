'use client';

import { ChevronDown } from 'lucide-react';

import { inputClasses } from '@/components/ui/input';
import { PAGE_TYPES, pageTypeLabel } from '@/lib/site-health/page-types';
import { cn } from '@/lib/utils';

/**
 * The page-type filter control (site-health v2 P1) shared by the pages,
 * inventory, and issues list screens. A native `<select>` on the shared
 * `inputClasses` control treatment (the same pattern as the Topics narrow
 * selector) — the empty option clears the filter (all page types).
 */
export function PageTypeSelect({
  value,
  onChange,
}: Readonly<{ value: string; onChange: (value: string) => void }>) {
  return (
    <div className="relative w-44">
      <select
        aria-label="Filter by page type"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={cn(inputClasses, 'appearance-none pr-9')}
      >
        <option value="">All page types</option>
        {PAGE_TYPES.map((pageType) => (
          <option key={pageType} value={pageType}>
            {pageTypeLabel(pageType)}
          </option>
        ))}
      </select>
      <ChevronDown
        className="text-muted pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2"
        aria-hidden
      />
    </div>
  );
}
