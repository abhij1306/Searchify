'use client';

import { Moon, Sun } from 'lucide-react';
import { useSyncExternalStore } from 'react';

import { cn } from '@/lib/utils';
import { applyTheme, readTheme, subscribeTheme } from '@/lib/theme';

/**
 * ThemeToggle — sets and persists the `data-theme` attribute on <html>.
 *
 * F1 primitive: a minimal, token-only button. F3 re-skins it on the shared
 * `Button` primitive; the theme logic (read/apply/subscribe) lives in
 * `lib/theme.ts` and is unit-tested there and here.
 */
export function ThemeToggle({ className }: Readonly<{ className?: string }>) {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => 'light');

  function toggleTheme() {
    applyTheme(theme === 'dark' ? 'light' : 'dark');
  }

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label="Toggle color theme"
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className={cn(
        'inline-flex size-8 items-center justify-center rounded-full border border-border bg-panel text-secondary transition-colors hover:bg-background-alt hover:text-foreground',
        className,
      )}
    >
      {theme === 'dark' ? (
        <Sun className="size-4" strokeWidth={2} aria-hidden />
      ) : (
        <Moon className="size-4" strokeWidth={2} aria-hidden />
      )}
    </button>
  );
}
