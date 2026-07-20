'use client';

import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * Tooltip (§8) — Radix. Surface = bg-well inverse-ish, --text-xs. Wrap the app
 * (or a subtree) once in <TooltipProvider>; each Tooltip pairs a trigger with
 * short text `content`.
 */
export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({
  children,
  content,
  side = 'top',
  align = 'center',
  className,
  delayDuration = 200,
}: Readonly<{
  children: ReactNode;
  content: ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
  align?: 'start' | 'center' | 'end';
  className?: string;
  delayDuration?: number;
}>) {
  return (
    <TooltipPrimitive.Root delayDuration={delayDuration}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          align={align}
          sideOffset={6}
          className={cn(
            'border-border-strong bg-well text-foreground z-[200] max-w-[min(320px,calc(100vw-24px))] rounded-md border px-2 py-1 text-xs leading-normal font-medium shadow-sm',
            className,
          )}
        >
          {content}
          <TooltipPrimitive.Arrow className="fill-well" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
