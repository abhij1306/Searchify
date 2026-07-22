'use client';

import { MessageSquarePlus } from 'lucide-react';

import { Button } from '@/components/ui/button';

/**
 * Empty state (F7) — shown when the active project's prompt set has no prompts.
 * Midnight empty-state pattern: mono eyebrow + display heading + ghost CTAs
 * inviting the first manual prompt or a CSV import.
 */
export function PromptEmptyState({
  onAdd,
  onImport,
}: Readonly<{ onAdd: () => void; onImport: () => void }>) {
  return (
    <div className="border-border bg-panel flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed px-6 py-16 text-center">
      <span className="bg-accent-subtle text-accent-text flex size-12 items-center justify-center rounded-full">
        <MessageSquarePlus className="size-6" aria-hidden />
      </span>
      <div className="grid max-w-sm gap-1">
        <p className="text-2xs text-muted font-mono font-medium tracking-[0.08em] uppercase">
          Prompt library
        </p>
        <h3 className="font-display text-foreground text-xl font-semibold">No prompts yet</h3>
        <p className="text-secondary mt-1 text-sm">
          Add the questions you want to track across AI engines. Enter them one at a time or import
          a CSV.
        </p>
      </div>
      <div className="flex gap-2">
        <Button variant="ghost" onClick={onAdd}>
          Add prompt
        </Button>
        <Button variant="ghost" onClick={onImport}>
          Import CSV
        </Button>
      </div>
    </div>
  );
}
