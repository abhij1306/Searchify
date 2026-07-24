'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useState } from 'react';

import { PromptLibrary } from '@/components/prompts/prompt-library';
import { YourPrompts } from '@/components/prompts/your-prompts';
import { Button } from '@/components/ui/button';
import { TooltipProvider } from '@/components/ui/tooltip';

/**
 * Prompts screen (design.md §9.4, sidebar "Prompts") — the single prompts
 * surface.
 *
 * Read view by default: the read-only, score-annotated view of the active
 * prompt configuration (prompts grouped by topic with expandable rows and
 * per-prompt / per-topic Visibility Score derived from persisted audit
 * evidence). "Manage prompts" mode swaps in the full management workspace
 * (add, import, review proposed/archived, AI generation) without leaving the
 * page. The mode follows the `?mode=manage` deep link (`/prompt-research`
 * redirects here, and the read view's manage controls are plain links to that
 * URL); the in-page toggle buttons set a local override so no navigation is
 * needed. The page title renders in the top bar (F5), so there is no in-page
 * header.
 */
function PromptsScreen() {
  const router = useRouter();
  const modeParam = useSearchParams().get('mode');
  // Local override for the in-page toggle buttons; null = follow the URL.
  const [override, setOverride] = useState<boolean | null>(null);
  const managing = override ?? modeParam === 'manage';

  // Exiting manage mode clears both the override and the URL param, so the
  // read view's `/prompts?mode=manage` links keep working (they would
  // otherwise self-reference the current URL and no-op).
  const exitManage = () => {
    setOverride(null);
    if (modeParam === 'manage') router.replace('/prompts');
  };

  if (managing) {
    return (
      <TooltipProvider>
        <div className="grid gap-6">
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={exitManage}>
              Done managing
            </Button>
          </div>
          <PromptLibrary />
        </div>
      </TooltipProvider>
    );
  }

  return (
    <div className="grid gap-6">
      <div className="flex justify-end">
        <Button variant="secondary" size="sm" onClick={() => setOverride(true)}>
          Manage prompts
        </Button>
      </div>
      <YourPrompts />
    </div>
  );
}

export default function PromptsPage() {
  // `PromptsScreen` reads `useSearchParams` (`?mode=manage` deep link), so it
  // sits under `<Suspense>` per Next's CSR-bailout requirement.
  return (
    <Suspense>
      <PromptsScreen />
    </Suspense>
  );
}
