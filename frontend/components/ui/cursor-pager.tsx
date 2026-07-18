import { Button } from '@/components/ui/button';

/**
 * Cursor-pagination button pair (Previous / Next) shared by the site-health
 * cursor-paged lists. Wrappers/layout stay with the caller; this owns only the
 * two buttons and their enabled state.
 */
export function CursorPager({
  canPrev,
  canNext,
  onPrev,
  onNext,
}: Readonly<{
  canPrev: boolean;
  canNext: boolean;
  onPrev: () => void;
  onNext: () => void;
}>) {
  return (
    <>
      <Button variant="secondary" size="sm" onClick={onPrev} disabled={!canPrev}>
        Previous
      </Button>
      <Button variant="secondary" size="sm" onClick={onNext} disabled={!canNext}>
        Next
      </Button>
    </>
  );
}
