import type {
  HTMLAttributes,
  ReactNode,
  Ref,
  TdHTMLAttributes,
  ThHTMLAttributes,
} from 'react';

import { cn } from '@/lib/utils';

/**
 * Dense analytics table (§8):
 *  - sticky 32px header (--table-header-height), --text-xs uppercase, tracking-wide
 *  - 40px rows (--table-row-height), --text-sm cells
 *  - hover row highlight; numeric columns (add `numeric`) center-align with
 *    tabular numerals, text/date columns stay left-aligned
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
    <div
      ref={wrapperRef}
      className={cn('relative w-full overflow-auto', wrapperClassName)}
    >
      <table
        className={cn(
          'w-full border-collapse text-[length:var(--table-font-size)]',
          className,
        )}
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
        'h-[var(--table-row-height)] border-b border-border-subtle bg-panel transition-colors hover:bg-accent-soft',
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
}: Readonly<
  ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }
>) {
  return (
    <th
      {...props}
      className={cn(
        'sticky top-0 z-10 h-[var(--table-header-height)] border-b border-border bg-background-alt px-3 align-middle text-[length:var(--table-header-font-size)] font-semibold uppercase tracking-wide text-muted',
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
}: Readonly<
  TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }
>) {
  return (
    <td
      {...props}
      className={cn(
        'px-3 py-0 align-middle text-foreground',
        numeric ? 'text-center tabular-nums' : 'text-left',
        className,
      )}
    >
      {children}
    </td>
  );
}
