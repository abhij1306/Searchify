import type { ReactNode } from 'react';

import { AuthBrandPanel, AuthWordmark } from '@/components/auth/brand-panel';
import { Card } from '@/components/ui/card';
import { ThemeToggle } from '@/components/ui/theme-toggle';

/**
 * Auth route-group layout (F4, midnight redesign Phase C).
 *
 * Split-screen shell per the approved mockup
 * (designs/auth-login-split-recommended.html): at ≥900px a two-column grid
 * [5fr 6fr] pairs the token-driven brand panel (components/auth/brand-panel)
 * with the centered auth card; below 900px only the form panel renders, with
 * a compact wordmark row above the card. Shared by `/login` and `/register`.
 *
 * The single-h1 rule: the pages own the only h1 — the wordmarks are spans,
 * and the brand headline is a `<p>`.
 */
export default function AuthLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <div className="bg-background min-h-dvh min-[900px]:grid min-[900px]:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <AuthBrandPanel />

      {/* ── Form panel ───────────────────────────────────────────────── */}
      <main className="bg-background relative flex min-h-dvh flex-col items-center justify-center px-6 py-12">
        <div className="absolute top-6 right-6">
          <ThemeToggle />
        </div>
        <div className="flex w-full max-w-[420px] flex-col items-center gap-6">
          <div className="min-[900px]:hidden">
            <AuthWordmark compact />
          </div>
          <Card className="shadow-card w-full max-w-[400px] p-6">{children}</Card>
        </div>
      </main>
    </div>
  );
}
