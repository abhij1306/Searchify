'use client';

import { PageTitle } from '@/components/ui/typography';
import { SetupForm } from '@/components/setup/setup-form';

/**
 * `/setup` (F6) — first-run / create Brand-Project setup.
 *
 * Renders the create form: on submit it POSTs a new project, sets it as the
 * active project (F5 context), and routes to `/visibility`. Editing an existing
 * project happens at `/setup/[projectId]`.
 */
export default function SetupPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <div className="mb-6">
        <PageTitle kicker="Setup">Create your brand project</PageTitle>
        <p className="mt-1 text-sm text-secondary">
          Tell us about your brand, competitors, and how audits should run. You can change all of
          this later.
        </p>
      </div>
      <SetupForm />
    </div>
  );
}
