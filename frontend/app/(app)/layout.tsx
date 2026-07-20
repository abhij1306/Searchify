'use client';

import type { ReactNode } from 'react';

import { AppShell } from '@/components/layout/app-shell';
import { Skeleton } from '@/components/ui/skeleton';
import { SessionGuard } from '@/lib/auth/session-guard';
import { ProjectProvider } from '@/lib/project/project-context';

/**
 * Authed-area layout (F5).
 *
 * Wraps every `(app)` route in F4's `<SessionGuard>` (bounces unauthenticated
 * visitors to `/login` and installs the 401 watchdog), then the F5
 * `<ProjectProvider>` (active-project context + `X-Workspace-Id` header wiring),
 * then the `<AppShell>` chrome (sidebar + top bar). The landing redirector at
 * `(app)/page.tsx` keeps its own guard for the pre-project routing case.
 */
export default function AppLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <SessionGuard fallback={<ShellFallback />}>
      <ProjectProvider>
        <AppShell>{children}</AppShell>
      </ProjectProvider>
    </SessionGuard>
  );
}

function ShellFallback() {
  return (
    <div className="bg-background flex min-h-dvh items-center justify-center p-6">
      <div className="grid w-full max-w-[280px] gap-3" aria-hidden>
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
      <span className="sr-only">Loading your workspace…</span>
    </div>
  );
}
