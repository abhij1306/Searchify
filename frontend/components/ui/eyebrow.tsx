import type { ComponentPropsWithoutRef } from 'react';

import { cn } from '@/lib/utils';

/**
 * Eyebrow (kicker) recipes — the midnight mono micro-label pattern
 * (IBM Plex Mono, 2xs, medium, tracked, uppercase).
 *
 * `eyebrowClasses` is the muted form, shared by page eyebrows, table
 * headers, panel labels and <CardEyebrow>; apply it to whatever element is
 * semantic at the call site. <AccentEyebrow> is the accent-dot page eyebrow
 * (dot + accent text) used atop setup and status pages.
 */
export const eyebrowClasses =
  'text-2xs text-muted font-mono font-medium tracking-[0.08em] uppercase';

export function AccentEyebrow({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'span'>>) {
  return (
    <span
      {...props}
      className={cn(eyebrowClasses, 'text-accent-text inline-flex items-center gap-1.5', className)}
    >
      <span className="bg-accent size-1.5 rounded-full" aria-hidden />
      {children}
    </span>
  );
}
