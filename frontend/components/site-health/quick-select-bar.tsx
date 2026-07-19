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
    <div className="flex flex-wrap items-center gap-2 rounded-md border border-border-subtle bg-background-alt px-3 py-2">
      <span className="text-xs font-medium text-secondary">Quick select</span>
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
          <span className="text-xs text-secondary">Deselect every page?</span>
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
      {pending ? <span className="text-xs text-muted">Applying…</span> : null}
    </div>
  );
}
