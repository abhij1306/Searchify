'use client';

import { Download, ExternalLink } from 'lucide-react';
import { usePathname } from 'next/navigation';

import { Button } from '@/components/ui/button';
import { ThemeToggle } from '@/components/ui/theme-toggle';
import { Tooltip } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

/** Docs/learn destination (external). Kept here so it is easy to retarget. */
const LEARN_URL = 'https://docs.searchify.example';

/**
 * Route → page title map. Exact paths first; dynamic segments are resolved by
 * longest-prefix match in `resolveTitle`. Page headers live here (not on the
 * pages themselves) so the top bar is the single title surface.
 */
const PAGE_TITLES: ReadonlyArray<readonly [prefix: string, title: string]> = [
  ['/visibility', 'Visibility'],
  ['/analytics', 'LLM Analytics'],
  ['/traffic', 'Traffic'],
  ['/prompts', 'Prompts'],
  ['/runs', 'Audits'],
  ['/content', 'Content'],
  ['/setup', 'Setup'],
  ['/knowledge-base', 'Knowledge Base'],
  ['/site-health', 'Site Health'],
  ['/issues', 'Issues'],
  ['/settings', 'Settings'],
  ['/providers', 'Settings'],
];

/** Deeper-route overrides (checked before the prefix table). */
const EXACT_OVERRIDES: ReadonlyArray<readonly [pattern: RegExp, title: string]> = [
  [/^\/runs\/[^/]+\/executions\/[^/]+$/, 'Execution Evidence'],
  [/^\/runs\/[^/]+$/, 'Run Detail'],
];

function resolveTitle(pathname: string): string {
  for (const [pattern, title] of EXACT_OVERRIDES) {
    if (pattern.test(pathname)) return title;
  }
  for (const [prefix, title] of PAGE_TITLES) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) return title;
  }
  return 'Searchify';
}

/**
 * TopBar (F5) — 52px bar (docs/design.md §9.2).
 *
 * Left: the current page's title (replaces the retired "Find anything…" search
 * placeholder — pages no longer render their own header blocks). Right: an
 * Export hook (a disabled placeholder wired for F10's run/report export), a
 * Learn link (external docs), and the theme toggle. The project switcher +
 * user menu live in the sidebar per the design prose.
 */
export function TopBar({ className }: Readonly<{ className?: string }>) {
  const pathname = usePathname();
  const title = resolveTitle(pathname);

  return (
    <header
      className={cn(
        'border-border bg-panel flex h-[52px] shrink-0 items-center gap-3 border-b px-4',
        className,
      )}
    >
      <h1 className="font-display text-foreground min-w-0 flex-1 truncate text-base font-semibold">
        {title}
      </h1>

      <div className="flex items-center gap-1">
        {/* Export hook: placeholder disabled at MVP; wired for F10 report export. */}
        <Tooltip content="Export is available from a run (coming with reports)">
          <span>
            <Button variant="ghost" size="sm" disabled aria-disabled="true">
              <Download className="size-4" aria-hidden />
              Export
            </Button>
          </span>
        </Tooltip>

        <Button variant="ghost" size="sm" asChild>
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
