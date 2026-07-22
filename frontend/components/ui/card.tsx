import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Card (§8) — bg-panel, hairline border, --radius-lg, --card-padding,
 * shadow-card (CUBE27 midnight card treatment; all values flow from tokens).
 * Composed from header / title / description / content slots.
 *
 * Optional mono-eyebrow header hook: render <CardEyebrow> above <CardTitle>
 * for the midnight panel-label pattern (IBM Plex Mono, uppercase, tracked,
 * muted) — e.g.
 *   <CardHeader><CardEyebrow>Visibility score</CardEyebrow><CardTitle>…
 */ export function Card({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'section'>>) {
  return (
    <section
      {...props}
      className={cn('border-border bg-panel shadow-card rounded-lg border', className)}
    >
      {children}
    </section>
  );
}

export function CardHeader({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'header'>>) {
  return (
    <header
      {...props}
      className={cn(
        'border-border-subtle flex flex-col gap-1 border-b p-[var(--card-padding)]',
        className,
      )}
    >
      {children}
    </header>
  );
}

/**
 * CardEyebrow — optional mono-eyebrow label for card headers (the midnight
 * panel-label pattern). Pair with CardTitle; never a heading element.
 */
export function CardEyebrow({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'span'>>) {
  return (
    <span
      {...props}
      className={cn(
        'text-2xs text-muted font-mono font-medium tracking-[0.08em] uppercase',
        className,
      )}
    >
      {children}
    </span>
  );
}

export function CardTitle({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'h3'>>) {
  return (
    <h3 {...props} className={cn('text-foreground text-lg font-semibold', className)}>
      {children}
    </h3>
  );
}

export function CardDescription({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'p'>>) {
  return (
    <p {...props} className={cn('text-secondary text-sm', className)}>
      {children}
    </p>
  );
}

export function CardContent({
  children,
  className,
  ...props
}: Readonly<ComponentPropsWithoutRef<'div'> & { children: ReactNode }>) {
  return (
    <div {...props} className={cn('p-[var(--card-padding)]', className)}>
      {children}
    </div>
  );
}
