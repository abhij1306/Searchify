import type { HTMLAttributes, ReactNode, Ref, TdHTMLAttributes, ThHTMLAttributes } from 'react';

import { eyebrowClasses } from '@/components/ui/eyebrow';
import { cn } from '@/lib/utils';

/**
 * Dense analytics table (§8):
 *  - sticky 32px header (--table-header-height); header cells are uppercase
 *    mono eyebrows (font-mono, text-2xs, tracking-[0.08em], muted — §8
 *    panel-label pattern)
 *  - 40px rows (--table-row-height), --text-sm cells
 *  - hover row highlight (accent-soft tint); numeric columns (add `numeric`)
 *    center-align with tabular numerals, text/date columns stay left-aligned
 * The wrapper is scroll-capable so the sticky header pins on vertical scroll.
 */
export function Table({
  children,
  className,
  wrapperClassName,
  wrapperRef,
}: Readonly<{
  children: ReactNode;
  className?: string;
  wrapperClassName?: string;
  wrapperRef?: Ref<HTMLDivElement>;
}>) {
  return (
    <div ref={wrapperRef} className={cn('relative w-full overflow-auto', wrapperClassName)}>
      <table
        className={cn('w-full border-collapse text-[length:var(--table-font-size)]', className)}
      >
        {children}
      </table>
    </div>
  );
}

export function TableHeader({
  children,
  className,
  ...props
}: Readonly<HTMLAttributes<HTMLTableSectionElement>>) {
  return (
    <thead {...props} className={cn(className)}>
      {children}
    </thead>
  );
}

export function TableBody({
  children,
  className,
  ...props
}: Readonly<HTMLAttributes<HTMLTableSectionElement>>) {
  return (
    <tbody {...props} className={cn(className)}>
      {children}
    </tbody>
  );
}

export function TableRow({
  children,
  className,
  ...props
}: Readonly<HTMLAttributes<HTMLTableRowElement>>) {
  return (
    <tr
      {...props}
      className={cn(
        'border-border-subtle bg-panel hover:bg-accent-soft/40 h-[var(--table-row-height)] border-b transition-colors',
        className,
      )}
    >
      {children}
    </tr>
  );
}

export function TableHead({
  children,
  className,
  numeric,
  ...props
}: Readonly<ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }>) {
  return (
    <th
      {...props}
      className={cn(
        eyebrowClasses,
        'border-border bg-background-alt sticky top-0 z-10 h-[var(--table-header-height)] border-b px-3 align-middle',
        numeric ? 'text-center tabular-nums' : 'text-left',
        className,
      )}
    >
      {children}
    </th>
  );
}

export function TableCell({
  children,
  className,
  numeric,
  ...props
}: Readonly<TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }>) {
  return (
    <td
      {...props}
      className={cn(
        'text-foreground px-3 py-0 align-middle',
        numeric ? 'text-center tabular-nums' : 'text-left',
        className,
      )}
    >
      {children}
    </td>
  );
}
