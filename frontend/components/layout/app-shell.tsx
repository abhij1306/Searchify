'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';

import { LogoCube } from '@/components/ui/logo-cube';
import { TooltipProvider } from '@/components/ui/tooltip';

import { GettingStartedCard } from './getting-started-card';
import { ProjectSwitcher } from './project-switcher';
import { SidebarNav } from './sidebar-nav';
import { TopBar } from './top-bar';
import { UserMenu } from './user-menu';

/**
 * AppShell (F5) — the authed-area chrome (docs/design.md §9.2).
 *
 * Fixed 240px left sidebar (`bg-sidebar`): brand row (LogoCube + display-font
 * "Searchify" over a mono uppercase "by CUBE27" sub-tag, per the approved
 * midnight mockup) → project switcher → Getting-Started card → grouped nav →
 * user menu pinned to the bottom. A 52px top bar plus an
 * independently-scrolling content region. Wrapped once in `<TooltipProvider>`
 * so the sidebar's "soon" tooltips and the top bar's Export hint work.
 *
 * Session + project context are provided one level up in `(app)/layout.tsx`, so
 * this component is pure chrome around `children`.
 */
export function AppShell({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <TooltipProvider>
      <div className="bg-background flex h-dvh overflow-hidden">
        <aside className="border-border bg-sidebar flex w-60 shrink-0 flex-col gap-4 border-r p-4">
          {/* Brand row — plain spans (never a heading: one h1 per page, and no
              heading may contain "Searchify"). */}
          <Link
            href="/visibility"
            aria-label="Searchify home"
            className="focus-ring -mx-1 flex items-center gap-2.5 rounded-md px-1 py-0.5 no-underline"
          >
            <LogoCube size={26} />
            <span className="flex min-w-0 flex-col">
              <span className="font-display text-foreground text-base leading-tight font-semibold tracking-tight">
                Searchify
              </span>
              <span className="text-2xs text-muted font-mono tracking-[0.18em] uppercase">
                by CUBE27
              </span>
            </span>
          </Link>
          <ProjectSwitcher />
          <GettingStartedCard />
          <div className="sidebar-scroll min-h-0 flex-1 overflow-y-auto">
            <SidebarNav />
          </div>
          <div className="border-border-subtle border-t pt-2">
            <UserMenu />
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main className="content-scroll min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-[1440px] p-[var(--content-gutter)]">
              {children}
            </div>
          </main>
        </div>
      </div>
    </TooltipProvider>
  );
}
