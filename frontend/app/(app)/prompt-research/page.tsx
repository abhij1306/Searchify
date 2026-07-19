'use client';

import { PageTitle } from '@/components/ui/typography';
import { TooltipProvider } from '@/components/ui/tooltip';
import { PromptLibrary } from '@/components/prompts/prompt-library';

/**
 * Prompt Research screen (design.md §9.4 sidebar "Prompt Research").
 *
 * The prompt management + AI-generation workspace: topics rail, Active /
 * Proposed / Archived review tabs, manual entry, CSV import, and the
 * consent-gated "Generate prompts & topics" dialog. The read-only,
 * score-annotated view of the configured prompts lives on `/prompts`
 * ("Your Prompts").
 */
export default function PromptResearchPage() {
  return (
    <TooltipProvider>
      <div className="grid gap-6">
        <div>
          <PageTitle kicker="Prompts">Prompt research</PageTitle>
          <p className="mt-1 max-w-2xl text-sm text-secondary">
            Build and refine your prompt library. Organize prompts by topic, generate suggestions
            with AI, and review proposed prompts before they go live in audits.
          </p>
        </div>
        <PromptLibrary />
      </div>
    </TooltipProvider>
  );
}
