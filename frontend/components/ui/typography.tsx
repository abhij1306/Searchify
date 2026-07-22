import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Display-heading recipes (Bricolage via `--font-display`) from the midnight
 * redesign: `displayHeadingLgClasses` for panel / empty-state headings,
 * `displayHeadingXlClasses` for page titles. These are class recipes, not
 * components — the call site keeps whichever heading element is semantic.
 */
export const displayHeadingLgClasses = 'font-display text-foreground text-lg font-semibold';
export const displayHeadingXlClasses = 'font-display text-foreground text-xl font-semibold';

/** Page heading with optional uppercase kicker/eyebrow. */
export function PageTitle({
  children,
  kicker,
  className,
}: Readonly<{ children: ReactNode; kicker?: string; className?: string }>) {
  return (
    <div className={cn('space-y-1', className)}>
      {kicker ? (
        <p className="text-2xs text-accent-text font-semibold tracking-wider uppercase">{kicker}</p>
      ) : null}
      <h1 className="text-foreground text-xl font-bold">{children}</h1>
    </div>
  );
}

/** Section heading (card / block level). */
export function SectionTitle({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'h2'>>) {
  return (
    <h2 {...props} className={cn('text-foreground text-lg font-semibold', className)}>
      {children}
    </h2>
  );
}

/** Uppercase micro-label. */
export function Label({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'span'>>) {
  return (
    <span
      {...props}
      className={cn('text-2xs text-muted font-semibold tracking-wide uppercase', className)}
    >
      {children}
    </span>
  );
}

/** Mono metric value with tabular numerals. */
export function Metric({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'span'>>) {
  return (
    <span {...props} className={cn('mono text-foreground', className)}>
      {children}
    </span>
  );
}
