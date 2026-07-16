'use client';

import { MessageSquarePlus } from 'lucide-react';

import { Button } from '@/components/ui/button';

/**
 * Empty state (F7) — shown when the active project's prompt set has no prompts.
 * A centered call-to-action card that invites adding the first prompt or
 * importing a CSV.
 */
export function PromptEmptyState({
  onAdd,
  onImport,
}: Readonly<{ onAdd: () => void; onImport: () => void }>) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border bg-panel px-6 py-16 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-accent-subtle text-accent-text">
        <MessageSquarePlus className="size-6" aria-hidden />
      </span>
      <div className="max-w-sm">
        <h3 className="text-base font-semibold text-foreground">No prompts yet</h3>
        <p className="mt-1 text-sm text-secondary">
          Add the questions you want to track across AI engines. Enter them one at a time or import
          a CSV.
        </p>
      </div>
      <div className="flex gap-2">
        <Button variant="primary" onClick={onAdd}>
          Add prompt
        </Button>
        <Button variant="secondary" onClick={onImport}>
          Import CSV
        </Button>
      </div>
    </div>
  );
}
