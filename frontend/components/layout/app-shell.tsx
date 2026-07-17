'use client';

import type { ReactNode } from 'react';

import { TooltipProvider } from '@/components/ui/tooltip';

import { GettingStartedCard } from './getting-started-card';
import { ProjectSwitcher } from './project-switcher';
import { SidebarNav } from './sidebar-nav';
import { TopBar } from './top-bar';
import { UserMenu } from './user-menu';

/**
 * AppShell (F5) — the authed-area chrome (docs/design.md §9.2).
 *
 * Fixed 240px left sidebar (`bg-sidebar`): project switcher → Getting-Started
 * card → grouped nav → user menu pinned to the bottom. A 52px top bar plus an
 * independently-scrolling content region. Wrapped once in `<TooltipProvider>`
 * so the sidebar's "soon" tooltips and the top bar's Export hint work.
 *
 * Session + project context are provided one level up in `(app)/layout.tsx`, so
 * this component is pure chrome around `children`.
 */
export function AppShell({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <TooltipProvider>
      <div className="flex h-dvh overflow-hidden bg-background">
        <aside className="flex w-60 shrink-0 flex-col gap-4 border-r border-border bg-sidebar p-4">
          <ProjectSwitcher />
          <GettingStartedCard />
          <div className="sidebar-scroll min-h-0 flex-1 overflow-y-auto">
            <SidebarNav />
          </div>
          <div className="border-t border-border-subtle pt-2">
            <UserMenu />
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-[1440px] p-[var(--content-gutter)]">{children}</div>
          </main>
        </div>
      </div>
    </TooltipProvider>
  );
}
