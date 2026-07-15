import { ThemeToggle } from '@/components/ui/theme-toggle';

/**
 * Placeholder landing page for the F1 scaffold. Real screens (auth, shell,
 * setup, prompts, providers, visibility, runs) arrive in later frontend
 * tasks. This exists so `next build` produces a route and the theme toggle
 * is reachable for smoke verification.
 */
export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-2xl flex-col items-center justify-center gap-6 p-8 text-center">
      <div className="absolute right-6 top-6">
        <ThemeToggle />
      </div>
      <h1 className="text-2xl font-bold tracking-tight text-foreground">Searchify</h1>
      <p className="text-sm text-secondary">
        AI-visibility analytics. Frontend scaffold ready — design tokens and theme wired.
      </p>
      <p className="mono text-xs text-muted">v1 · visibility MVP</p>
    </main>
  );
}
