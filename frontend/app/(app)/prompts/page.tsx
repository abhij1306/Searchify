'use client';

import { PageTitle } from '@/components/ui/typography';
import { TooltipProvider } from '@/components/ui/tooltip';
import { PromptLibrary } from '@/components/prompts/prompt-library';

/**
 * Prompt library screen (F7, design.md §9.4).
 *
 * A prompt table (text / theme / intent / branded / enabled) with add / edit /
 * delete, an enable/disable toggle, search + filter, manual entry and in-browser
 * CSV import (preview → persist via `/prompt-sets/{id}/import`), plus the
 * AI-suggest coming-soon panel (decision B-4). Prompts are scoped to the active
 * project's prompt set (F5 context); a set is created on demand when a project
 * has none yet.
 */
export default function PromptsPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <div>
          <PageTitle kicker="Prompts">Your prompts</PageTitle>
          <p className="mt-1 max-w-2xl text-sm text-secondary">
            The questions Searchify asks each AI engine when it runs an audit. Add prompts manually
            or import a CSV, tag them by theme and intent, and toggle which ones are active.
          </p>
        </div>
        <PromptLibrary />
      </div>
    </TooltipProvider>
  );
}
