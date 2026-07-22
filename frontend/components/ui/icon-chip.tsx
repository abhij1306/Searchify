import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '@/lib/utils';

/**
 * IconChip — the midnight empty-state icon well: a 48px accent-subtle disc
 * centered around a lucide icon (icon itself stays `size-6` at the call
 * site). Purely decorative — the surrounding copy carries the meaning.
 */
export function IconChip({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'span'>>) {
  return (
    <span
      {...props}
      aria-hidden="true"
      className={cn(
        'bg-accent-subtle text-accent-text flex size-12 items-center justify-center rounded-full',
        className,
      )}
    >
      {children}
    </span>
  );
}
