import { KeyRound, Lock } from 'lucide-react';
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
          <div className="flex flex-col items-center gap-4 min-[900px]:hidden">
            <AuthWordmark compact />
            {/* Compact value statement per the mock's 390px frame — the
                single-h1 rule keeps this a <p> (pages own the only h1). */}
            <p className="text-center">
              <span className="text-accent-text font-mono text-2xs font-medium tracking-[0.16em] uppercase">
                <span aria-hidden="true" className="bg-accent mr-2 inline-block size-1.5 rounded-full" />
                Answer-engine optimization
              </span>
              <span className="font-display text-foreground mt-2 block text-lg font-bold tracking-tight">
                See how <span className="text-gradient">AI answers</span> talk about your brand.
              </span>
            </p>
          </div>
          <Card className="shadow-card w-full max-w-[400px] p-6">{children}</Card>
          {/* BYOK trust microcopy under the card (mock: auth-trust-microcopy,
              shown on both the desktop and 390px frames). */}
          <div className="text-muted flex items-center justify-center gap-2 font-mono text-[11px]">
            <span className="inline-flex items-center gap-1.5">
              <KeyRound className="size-[13px]" strokeWidth={1.8} aria-hidden />
              Bring your own API keys
            </span>
            <span aria-hidden="true" className="opacity-60">
              ·
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Lock className="size-[13px]" strokeWidth={1.8} aria-hidden />
              Encrypted at rest
            </span>
          </div>
        </div>
      </main>
    </div>
  );
}
