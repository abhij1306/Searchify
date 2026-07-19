'use client';

import { PageTitle } from '@/components/ui/typography';
import { YourPrompts } from '@/components/prompts/your-prompts';

/**
 * Your Prompts screen (design.md §9.4, sidebar "Your Prompts").
 *
 * The read-only, score-annotated view of the active prompt configuration:
 * prompts grouped by topic with expandable rows and per-prompt / per-topic
 * Visibility Score derived from persisted audit evidence. Management (add,
 * import, review proposed/archived, AI generation) lives on `/prompt-research`.
 */
export default function PromptsPage() {
  return (
    <div className="grid gap-6">
      <div>
        <PageTitle kicker="Prompts">Your prompts</PageTitle>
        <p className="mt-1 max-w-2xl text-sm text-secondary">
          The active prompts Searchify asks each AI engine when it runs an audit, grouped by topic
          with their measured visibility.
        </p>
      </div>
      <YourPrompts />
    </div>
  );
}
