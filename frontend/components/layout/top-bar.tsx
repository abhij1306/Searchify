'use client';

import { Download, ExternalLink, Search } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ThemeToggle } from '@/components/ui/theme-toggle';
import { Tooltip } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/** Docs/learn destination (external). Kept here so it is easy to retarget. */
const LEARN_URL = 'https://docs.searchify.example';

/**
 * TopBar (F5) — 52px bar (docs/design.md §9.2).
 *
 * Left: a non-functional "Find anything…" search placeholder (MVP scope — no
 * search backend yet). Right: an Export hook (a disabled placeholder wired for
 * F10's run/report export), a Learn link (external docs), and the theme toggle.
 * The project switcher + user menu live in the sidebar per the design prose.
 */
export function TopBar({ className }: Readonly<{ className?: string }>) {
  return (
    <header
      className={cn(
        'flex h-[52px] shrink-0 items-center gap-3 border-b border-border bg-panel px-4',
        className,
      )}
    >
      <label className="flex min-w-0 flex-1 items-center gap-2 text-muted" aria-label="Search">
        <Search className="size-4 shrink-0" aria-hidden />
        <input
          type="search"
          placeholder="Find anything…"
          disabled
          aria-disabled="true"
          className="w-full max-w-sm bg-transparent text-sm text-foreground placeholder:text-muted focus:outline-none disabled:cursor-not-allowed"
        />
      </label>

      <div className="flex items-center gap-1">
        {/* Export hook: placeholder disabled at MVP; wired for F10 report export. */}
        <Tooltip content="Export is available from a run (coming with reports)">
          <span>
            <Button variant="topbar" size="sm" disabled aria-disabled="true">
              <Download className="size-4" aria-hidden />
              Export
            </Button>
          </span>
        </Tooltip>

        <Button variant="topbar" size="sm" asChild>
          <a href={LEARN_URL} target="_blank" rel="noreferrer noopener">
            <ExternalLink className="size-4" aria-hidden />
            Learn
          </a>
        </Button>

        <ThemeToggle />
      </div>
    </header>
  );
}
