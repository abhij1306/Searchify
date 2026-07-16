import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '@/lib/utils';

/** Page heading with optional uppercase kicker/eyebrow. */
export function PageTitle({
  children,
  kicker,
  className,
}: Readonly<{ children: ReactNode; kicker?: string; className?: string }>) {
  return (
    <div className={cn('space-y-1', className)}>
      {kicker ? (
        <p className="text-2xs font-semibold uppercase tracking-wider text-accent-text">
          {kicker}
        </p>
      ) : null}
      <h1 className="text-xl font-bold text-foreground">{children}</h1>
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
    <h2 {...props} className={cn('text-lg font-semibold text-foreground', className)}>
      {children}
    </h2>
  );
}

/** Secondary supporting text. */
export function Subtitle({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'p'>>) {
  return (
    <p {...props} className={cn('text-sm leading-normal text-secondary', className)}>
      {children}
    </p>
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
      className={cn('text-2xs font-semibold uppercase tracking-wide text-muted', className)}
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
