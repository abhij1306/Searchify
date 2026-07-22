import { Eye, KeyRound, Sigma } from 'lucide-react';
import Link from 'next/link';

import { LogoCube } from '@/components/ui/logo-cube';
import { cn } from '@/lib/utils';

/**
 * Auth brand panel (midnight redesign Phase C) — the decorative left column
 * of the split-screen auth shell (designs/auth-login-split-recommended.html):
 * wordmark, eyebrow + display headline, proof points, a static decorative
 * mini stat card, and a footer strip. Hidden below 900px, where the form
 * panel renders <AuthWordmark compact> above the auth card instead.
 *
 * The single-h1 rule: the auth pages own the only h1 — the wordmarks here
 * are spans, and the brand headline is a `<p>`.
 */

const PROOF_POINTS = [
  {
    icon: Sigma,
    lead: 'Deterministic scoring.',
    rest: 'Same data, same score — every run.',
  },
  {
    icon: KeyRound,
    lead: 'BYOK privacy.',
    rest: 'Your provider keys, Fernet-encrypted at rest.',
  },
  {
    icon: Eye,
    lead: 'Evidence explorer.',
    rest: 'Every number drills to its raw run.',
  },
] as const;

// Static share-of-voice rows — the same fictional Acme dataset as the landing
// hero's product visual. Purely illustrative (the card is aria-hidden).
const SOV_ROWS = [
  { name: 'Acme', value: 42, brand: true },
  { name: 'Northwind', value: 27, brand: false },
  { name: 'Contoso', value: 19, brand: false },
  { name: 'Globex', value: 12, brand: false },
] as const;

export function AuthWordmark({ compact = false }: Readonly<{ compact?: boolean }>) {
  return (
    <Link
      href="/"
      aria-label="Searchify home"
      className="focus-ring inline-flex items-center gap-2.5 rounded-md no-underline"
    >
      <LogoCube size={compact ? 24 : 28} />
      <span
        className={cn(
          'font-display text-foreground font-bold tracking-tight',
          compact ? 'text-base' : 'text-lg',
        )}
      >
        Searchify
      </span>
      {compact ? null : (
        <span className="border-border text-muted text-2xs rounded-full border px-2 py-0.5 font-mono tracking-[0.18em] uppercase">
          By CUBE27
        </span>
      )}
    </Link>
  );
}

export function AuthBrandPanel() {
  return (
    <aside className="bg-panel border-border relative flex flex-col overflow-hidden border-r p-12 max-[900px]:hidden">
      <div className="auth-aurora" aria-hidden="true" />
      <div className="auth-grain" aria-hidden="true" />

      <div className="relative flex flex-1 flex-col">
        <AuthWordmark />

        <div className="mt-16 max-w-[480px]">
          <p className="text-accent-text font-mono text-xs font-medium tracking-[0.16em] uppercase">
            <span
              aria-hidden="true"
              className="bg-accent mr-2 inline-block size-1.5 rounded-full"
            />
            Answer-engine optimization
          </p>
          <p className="font-display text-foreground mt-4 text-2xl font-bold tracking-tight">
            See how <span className="text-gradient">AI answers</span> talk about your brand.
          </p>

          <div className="mt-8 grid gap-4">
            {PROOF_POINTS.map((proof) => (
              <div key={proof.lead} className="flex items-start gap-3">
                <span className="bg-elevated border-border text-accent-text grid size-[34px] shrink-0 place-items-center rounded-lg border">
                  <proof.icon className="size-4" strokeWidth={1.8} aria-hidden />
                </span>
                <p className="text-secondary text-sm">
                  <strong className="text-foreground font-semibold">{proof.lead}</strong>{' '}
                  {proof.rest}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Mini stat card — static decorative echo of the landing hero's
            fictional Acme dataset; never interactive, always aria-hidden. */}
        <div
          aria-hidden="true"
          className="bg-elevated border-border shadow-card mt-auto mb-8 w-full max-w-[520px] rounded-2xl border p-4"
        >
          <div className="mb-3 flex items-center gap-2.5 px-0.5">
            <span className="bg-accent text-accent-fg text-2xs grid size-[22px] shrink-0 place-items-center rounded-md font-bold">
              A
            </span>
            <span className="text-foreground text-xs font-semibold">
              Acme Corp <span className="text-muted">▾</span>
            </span>
            <span className="border-border bg-panel text-secondary text-2xs rounded-full border px-2.5 py-0.5 font-mono">
              03 audit · 240 runs
            </span>
            <span className="flex-1" />
            <span className="border-border bg-panel text-secondary text-2xs inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono">
              <span className="bg-accent size-1.5 rounded-full" />
              Last 30 days
            </span>
          </div>

          <div className="grid grid-cols-[5fr_7fr] gap-3">
            <div className="border-border bg-panel rounded-xl border p-3.5">
              <p className="text-muted text-2xs font-mono tracking-[0.16em] uppercase">
                Visibility score
              </p>
              <p className="mt-1 flex items-baseline gap-2">
                <span className="mono text-foreground text-2xl font-bold">68</span>
                <span className="mono text-muted text-2xs">/100</span>
                <span className="bg-info-bg text-info-text text-2xs rounded-full px-2 py-0.5 font-mono">
                  ▲ 12 pts
                </span>
              </p>
              <svg
                className="mt-2 block h-12 w-full"
                viewBox="0 0 300 64"
                preserveAspectRatio="none"
              >
                <path
                  className="fill-accent-soft"
                  d="M0 50 C 24 46, 36 47, 54 42 S 92 34, 114 36 S 150 44, 172 38 S 214 26, 236 22 S 276 12, 300 10 L300 64 L0 64 Z"
                />
                <path
                  className="stroke-foreground"
                  d="M0 50 C 24 46, 36 47, 54 42 S 92 34, 114 36 S 150 44, 172 38 S 214 26, 236 22 S 276 12, 300 10"
                  fill="none"
                  strokeWidth="2"
                />
              </svg>
              <p className="border-border text-muted text-2xs mt-2 flex gap-3 border-t pt-2 font-mono">
                <span>
                  <b className="text-secondary font-medium">128</b> mentions
                </span>
                <span>
                  <b className="text-secondary font-medium">96</b> citations
                </span>
              </p>
            </div>

            <div className="border-border bg-panel rounded-xl border p-3.5">
              <p className="flex items-baseline justify-between gap-2">
                <span className="text-muted text-2xs font-mono tracking-[0.16em] uppercase">
                  Share of voice
                </span>
                <span className="text-muted text-2xs font-mono">mentions across engines</span>
              </p>
              <div className="mt-3 grid gap-2.5">
                {SOV_ROWS.map((row) => (
                  <div
                    key={row.name}
                    className="grid grid-cols-[64px_1fr_34px] items-center gap-2.5"
                  >
                    <span
                      className={cn(
                        'text-xs',
                        row.brand ? 'text-foreground font-semibold' : 'text-secondary font-medium',
                      )}
                    >
                      {row.name}
                    </span>
                    <span className="bg-border h-1.5 overflow-hidden rounded-full">
                      <span
                        className={cn(
                          'block h-full rounded-full',
                          row.brand ? 'auth-sov-brand' : 'bg-muted',
                        )}
                        style={{ width: `${row.value}%` }}
                      />
                    </span>
                    <span
                      className={cn(
                        'mono text-2xs text-right',
                        row.brand ? 'text-foreground' : 'text-secondary',
                      )}
                    >
                      {row.value}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="text-muted text-2xs flex items-center justify-between font-mono tracking-[0.14em] uppercase">
          <span>
            <span aria-hidden="true" className="bg-accent mr-2 inline-block size-1 rounded-full" />
            Measured where answers happen
          </span>
          <span>© 2026 CUBE27</span>
        </div>
      </div>
    </aside>
  );
}
