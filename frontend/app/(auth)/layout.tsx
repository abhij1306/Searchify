import Link from 'next/link';
import type { ReactNode } from 'react';

import { Card } from '@/components/ui/card';
import { ThemeToggle } from '@/components/ui/theme-toggle';

/**
 * Auth route-group layout (F4).
 *
 * Centered single-card shell on `bg-base` with the Searchify wordmark and a
 * corner theme toggle — no sidebar / top-bar (design.md §9.1). Shared by
 * `/login` and `/register`.
 */
export default function AuthLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <main className="relative flex min-h-dvh flex-col items-center justify-center gap-6 bg-background p-6">
      <div className="absolute right-6 top-6">
        <ThemeToggle />
      </div>
      <Link
        href="/"
        className="focus-ring rounded-md text-2xl font-bold tracking-tight text-foreground no-underline"
      >
        Searchify
      </Link>
      <Card className="w-full max-w-[400px] p-6 shadow-card">{children}</Card>
    </main>
  );
}
