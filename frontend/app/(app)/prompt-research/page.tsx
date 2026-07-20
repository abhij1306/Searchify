'use client';

import { TooltipProvider } from '@/components/ui/tooltip';
import { PromptLibrary } from '@/components/prompts/prompt-library';

/**
 * Prompt Research screen (design.md §9.4 sidebar "Prompt Research").
 *
 * The prompt management + AI-generation workspace: topics rail, Active /
 * Proposed / Archived review tabs, manual entry, CSV import, and the
 * consent-gated "Generate prompts & topics" dialog. The read-only,
 * score-annotated view of the configured prompts lives on `/prompts`
 * ("Your Prompts"). The page title renders in the top bar (F5), so there is
 * no in-page header.
 */
export default function PromptResearchPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <PromptLibrary />
      </div>
    </TooltipProvider>
  );
}
