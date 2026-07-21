'use client';

import { useState } from 'react';

/**
 * Keyset-pagination cursor stack shared by the Site Health tables
 * (discovering inventory, selection inventory, scored page tabs).
 *
 * Prev pops; Next pushes idempotently — under rapid clicks the captured
 * nextCursor may already be on the stack before the rerender lands, so a
 * duplicate push is dropped instead of double-advancing.
 */
export function useCursorStack() {
  const [stack, setStack] = useState<string[]>([]);
  const cursor = stack.at(-1) ?? undefined;
  const canPrev = stack.length > 0;

  const push = (nextCursor: string | null) => {
    if (!nextCursor) return;
    setStack((prev) => (prev.at(-1) === nextCursor ? prev : [...prev, nextCursor]));
  };
  const pop = () => setStack((prev) => prev.slice(0, -1));
  const reset = () => setStack([]);

  return { cursor, canPrev, push, pop, reset };
}
