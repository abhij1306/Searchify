'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { History, X } from 'lucide-react';
import { useEffect, useState } from 'react';

import { cn } from '@/lib/utils';
import { Badge } from './badge';
import { Button } from './button';
import type { RunStatusValue } from './badge-variants';

export type HistoryItem = {
  id: string;
  status: RunStatusValue;
  createdAt: string;
  label?: string;
  meta?: string;
};

/**
 * History drawer (§8) — right-side Radix drawer for run history / execution
 * lists. Each item is a selectable button with a run-status badge.
 */
export function HistoryDrawer({
  open,
  onOpenChange,
  items,
  activeId,
  onSelect,
  title = 'Run history',
}: Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  items: HistoryItem[];
  activeId?: string | null;
  onSelect: (id: string) => void;
  title?: string;
}>) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="bg-overlay-scrim fixed inset-0 z-[100]" />
        <DialogPrimitive.Content
          className={cn(
            'border-border bg-elevated shadow-modal-value fixed top-0 right-0 z-[101] flex h-full w-[380px] max-w-full flex-col border-l focus:outline-none',
          )}
        >
          <header className="border-border-subtle flex items-center justify-between gap-2 border-b px-4 py-3">
            <div className="flex min-w-0 items-center gap-2">
              <History className="text-muted size-4 shrink-0" aria-hidden />
              <DialogPrimitive.Title className="text-foreground truncate text-base font-semibold">
                {title}
              </DialogPrimitive.Title>
            </div>
            <DialogPrimitive.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close drawer">
                <X className="size-4" aria-hidden />
              </Button>
            </DialogPrimitive.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-auto">
            {items.length === 0 ? (
              <div className="text-muted flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
                <History className="size-8 opacity-20" aria-hidden />
                <p className="text-xs">No history found.</p>
              </div>
            ) : (
              <ul className="divide-border-subtle divide-y">
                {items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => onSelect(item.id)}
                      className={cn(
                        'hover:bg-background-alt flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors',
                        activeId === item.id && 'bg-background-alt',
                      )}
                    >
                      <div className="flex w-full items-center justify-between gap-2">
                        <span className="mono text-accent-text text-xs font-medium">
                          #{item.id.slice(0, 8)}
                        </span>
                        <Badge variant="run-status" value={item.status}>
                          {item.status}
                        </Badge>
                      </div>
                      {item.label ? (
                        <div className="text-foreground truncate text-sm font-medium">
                          {item.label}
                        </div>
                      ) : null}
                      <div className="text-muted flex w-full items-center justify-between text-xs">
                        <span>{item.meta ?? 'No details'}</span>
                        <ShortDate value={item.createdAt} />
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/**
 * Locale/timezone-dependent date, formatted only after mount so server-rendered
 * markup can never disagree with the browser's locale (hydration safety).
 */
function ShortDate({ value }: Readonly<{ value: string }>) {
  const [text, setText] = useState('');
  useEffect(() => {
    // Post-mount on purpose: locale formatting must not run during SSR/hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setText(formatShortDate(value));
  }, [value]);
  return <span className="mono">{text}</span>;
}

function formatShortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
