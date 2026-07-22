'use client';

import { SetupForm } from '@/components/setup/setup-form';
import { AccentEyebrow } from '@/components/ui/eyebrow';
import { displayHeadingXlClasses } from '@/components/ui/typography';

/**
 * `/setup/new` (F6) — explicitly create another Brand-Project.
 *
 * `/setup` itself opens the active project's edit form once one exists, so
 * this route is the deliberate "New project" entry point (project switcher).
 * The static `new` segment wins over the sibling `[projectId]` dynamic route.
 */
export default function NewProjectPage() {
  return (
    <div className="mx-auto grid max-w-3xl gap-5">
      <div className="grid gap-1">
        <AccentEyebrow>New project</AccentEyebrow>
        <h2 className={displayHeadingXlClasses}>Set up another brand project</h2>
        <p className="text-secondary text-sm">
          Five short steps — brand, market, domains, competitors, and audit defaults.
        </p>
      </div>
      <SetupForm />
    </div>
  );
}
