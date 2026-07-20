'use client';

import { PageTitle } from '@/components/ui/typography';
import { ContentScreen } from '@/components/content/content-screen';

/**
 * Content screen (sidebar "Content", Actions group).
 *
 * Prompt-box-first AI content generation grounded in the project's crawled
 * website evidence: describe the page, optionally include Website context,
 * generate, and copy the sanitised Markdown result. History lists recent
 * generations for the active project.
 */
export default function ContentPage() {
  return (
    <div className="grid gap-6">
      <div>
        <PageTitle kicker="Actions">Content</PageTitle>
        <p className="mt-1 max-w-2xl text-sm text-secondary">
          Generate website content grounded in your crawled site evidence, ready to review and
          publish.
        </p>
      </div>
      <ContentScreen />
    </div>
  );
}
