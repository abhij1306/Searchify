'use client';

import { SetupForm } from '@/components/setup/setup-form';

/**
 * `/setup` (F6) — first-run / create Brand-Project setup.
 *
 * Renders the create form: on submit it POSTs a new project, sets it as the
 * active project (F5 context), and routes to `/visibility`. Editing an existing
 * project happens at `/setup/[projectId]`. The page title renders in the top
 * bar (F5), so there is no in-page header.
 */
export default function SetupPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <SetupForm />
    </div>
  );
}
