'use client';

import { SetupForm } from '@/components/setup/setup-form';

/**
 * `/setup/new` (F6) — explicitly create another Brand-Project.
 *
 * `/setup` itself opens the active project's edit form once one exists, so
 * this route is the deliberate "New project" entry point (project switcher).
 * The static `new` segment wins over the sibling `[projectId]` dynamic route.
 */
export default function NewProjectPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <SetupForm />
    </div>
  );
}
