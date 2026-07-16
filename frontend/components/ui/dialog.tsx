'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';
import { Button } from './button';

/**
 * Dialog (§8) — Radix modal. Scrim = --overlay-scrim, surface = bg-elevated,
 * shadow-modal, --radius-xl. Header/body/footer slots; built-in close button.
 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}>) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[100] bg-overlay-scrim" />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-[101] flex max-h-[85vh] w-[640px] max-w-[92vw] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-elevated shadow-modal-value focus:outline-none',
            className,
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border-subtle px-5 py-4">
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-lg font-semibold text-foreground">
                {title}
              </DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-1 text-sm text-secondary">
                  {description}
                </DialogPrimitive.Description>
              ) : null}
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close dialog">
                <X className="size-4" aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-auto px-5 py-4">{children}</div>
          {footer ? (
            <footer className="flex items-center justify-end gap-2 border-t border-border-subtle px-5 py-4">
              {footer}
            </footer>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
