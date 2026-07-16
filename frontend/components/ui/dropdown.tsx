'use client';

import * as DropdownPrimitive from '@radix-ui/react-dropdown-menu';
import { Check } from 'lucide-react';
import type { ComponentPropsWithoutRef, ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Dropdown (§8) — Radix menu. Surface = bg-elevated, border, shadow-elevated.
 * Re-exports the Radix parts with token-styled Content / Item defaults.
 */
export const Dropdown = DropdownPrimitive.Root;
export const DropdownTrigger = DropdownPrimitive.Trigger;
export const DropdownSeparator = DropdownPrimitive.Separator;

export function DropdownContent({
  className,
  align = 'start',
  sideOffset = 4,
  children,
  ...props
}: Readonly<ComponentPropsWithoutRef<typeof DropdownPrimitive.Content>>) {
  return (
    <DropdownPrimitive.Portal>
      <DropdownPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          'z-[300] min-w-[10rem] overflow-hidden rounded-md border border-border bg-elevated p-1 shadow-elevated focus:outline-none',
          className,
        )}
        {...props}
      >
        {children}
      </DropdownPrimitive.Content>
    </DropdownPrimitive.Portal>
  );
}

export function DropdownItem({
  className,
  children,
  ...props
}: Readonly<ComponentPropsWithoutRef<typeof DropdownPrimitive.Item>>) {
  return (
    <DropdownPrimitive.Item
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-sm px-2.5 py-1.5 text-sm text-foreground outline-none transition-colors data-[highlighted]:bg-background-alt data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      {children}
    </DropdownPrimitive.Item>
  );
}

export function DropdownCheckboxItem({
  className,
  children,
  ...props
}: Readonly<ComponentPropsWithoutRef<typeof DropdownPrimitive.CheckboxItem>>) {
  return (
    <DropdownPrimitive.CheckboxItem
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-sm py-1.5 pl-7 pr-2.5 text-sm text-foreground outline-none transition-colors data-[highlighted]:bg-background-alt data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      <span className="absolute left-2 flex size-3.5 items-center justify-center">
        <DropdownPrimitive.ItemIndicator>
          <Check className="size-3.5 text-accent" aria-hidden />
        </DropdownPrimitive.ItemIndicator>
      </span>
      {children}
    </DropdownPrimitive.CheckboxItem>
  );
}

export function DropdownLabel({
  className,
  children,
}: Readonly<{ className?: string; children: ReactNode }>) {
  return (
    <DropdownPrimitive.Label
      className={cn('px-2.5 py-1.5 text-2xs font-semibold uppercase tracking-wide text-muted', className)}
    >
      {children}
    </DropdownPrimitive.Label>
  );
}
