'use client';

import { ContentScreen } from '@/components/content/content-screen';

/**
 * Content screen (sidebar "Content", Actions group).
 *
 * Prompt-box-first AI content generation grounded in the project's crawled
 * website evidence: describe the page, optionally include Website context,
 * generate, and copy the sanitised Markdown result. History lists recent
 * generations for the active project. The page title renders in the top bar
 * (F5), so there is no in-page header.
 */
export default function ContentPage() {
  return (
    <div className="grid gap-6">
      <ContentScreen />
    </div>
  );
}
