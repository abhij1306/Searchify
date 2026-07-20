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
    <main className="bg-background relative flex min-h-dvh flex-col items-center justify-center gap-6 p-6">
      <div className="absolute top-6 right-6">
        <ThemeToggle />
      </div>
      <Link
        href="/"
        className="focus-ring text-foreground rounded-md text-2xl font-bold tracking-tight no-underline"
      >
        Searchify
      </Link>
      <Card className="shadow-card w-full max-w-[400px] p-6">{children}</Card>
    </main>
  );
}
