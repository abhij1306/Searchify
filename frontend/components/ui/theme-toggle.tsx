'use client';

import { Moon, Sun } from 'lucide-react';
import { useSyncExternalStore } from 'react';

import { applyTheme, readTheme, subscribeTheme } from '@/lib/theme';
import { Button } from './button';

/**
 * ThemeToggle — sets and persists the `data-theme` attribute on <html>.
 *
 * F3: re-skinned onto the shared `Button` primitive (ghost/icon). The theme
 * logic (read/apply/subscribe) lives in `lib/theme.ts` and is unit-tested
 * there and here.
 */
export function ThemeToggle({ className }: Readonly<{ className?: string }>) {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => 'light');

  function toggleTheme() {
    applyTheme(theme === 'dark' ? 'light' : 'dark');
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label="Toggle color theme"
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className={className}
    >
      {theme === 'dark' ? (
        <Sun className="size-4" strokeWidth={2} aria-hidden />
      ) : (
        <Moon className="size-4" strokeWidth={2} aria-hidden />
      )}
    </Button>
  );
}
