'use client';

import { YourPrompts } from '@/components/prompts/your-prompts';

/**
 * Your Prompts screen (design.md §9.4, sidebar "Your Prompts").
 *
 * The read-only, score-annotated view of the active prompt configuration:
 * prompts grouped by topic with expandable rows and per-prompt / per-topic
 * Visibility Score derived from persisted audit evidence. Management (add,
 * import, review proposed/archived, AI generation) lives on `/prompt-research`.
 * The page title renders in the top bar (F5), so there is no in-page header.
 */
export default function PromptsPage() {
  return (
    <div className="grid gap-6">
      <YourPrompts />
    </div>
  );
}
