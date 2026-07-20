'use client';

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { BulkSelectMode } from '@/lib/site-health/use-monitored-selection';

/**
 * Quick-select bar: server-resolved bulk selection (first N / all / clear).
 * The ids are resolved on the server in the inventory's deterministic order,
 * so "first N" always matches the first N rows shown (under the same search
 * filter). Applies immediately — no separate commit step. "Clear all" is
 * destructive (wipes the committed selection immediately), so it requires a
 * second explicit click to confirm.
 */
export function QuickSelectBar({
  maxCount,
  pending,
  onBulkSelect,
}: Readonly<{
  maxCount: number;
  pending: boolean;
  onBulkSelect: (mode: BulkSelectMode, count?: number) => void;
}>) {
  const [bulkCount, setBulkCount] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);

  const parsedBulkCount = (() => {
    const n = Number.parseInt(bulkCount, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  })();

  return (
    <div className="border-border-subtle bg-background-alt flex flex-wrap items-center gap-2 rounded-md border px-3 py-2">
      <span className="text-secondary text-xs font-medium">Quick select</span>
      <Input
        type="number"
        min={1}
        max={maxCount}
        value={bulkCount}
        onChange={(event) => setBulkCount(event.target.value)}
        className="w-24"
        aria-label="Number of pages to select"
      />
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={pending || !parsedBulkCount}
        onClick={() => parsedBulkCount && onBulkSelect('first_n', parsedBulkCount)}
      >
        Select first {parsedBulkCount ?? 'N'}
      </Button>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={pending}
        onClick={() => onBulkSelect('all')}
      >
        Select all
      </Button>
      {confirmClear ? (
        <>
          <span className="text-secondary text-xs">Deselect every page?</span>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            disabled={pending}
            onClick={() => {
              setConfirmClear(false);
              onBulkSelect('none');
            }}
          >
            Confirm clear
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setConfirmClear(false)}>
            Cancel
          </Button>
        </>
      ) : (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={pending}
          onClick={() => setConfirmClear(true)}
        >
          Clear all
        </Button>
      )}
      {pending ? <span className="text-muted text-xs">Applying…</span> : null}
    </div>
  );
}
