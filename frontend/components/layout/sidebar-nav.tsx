'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { Tooltip } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

import { NAV_GROUPS, type NavItem } from './nav-items';

/**
 * SidebarNav (F5) — grouped sidebar navigation (docs/design.md §9.2).
 *
 * MVP-live items are `<Link>`s with active-route highlighting (accent-subtle bg
 * + accent-text). Roadmap items are rendered but **disabled**: a non-navigable
 * `<span>` (no href, `aria-disabled`) with a muted "soon" pill and an
 * explanatory tooltip. Highlighting matches the current route or any nested
 * route (e.g. `/runs/[id]` highlights Runs).
 */
function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function LiveItem({ item, active }: Readonly<{ item: NavItem; active: boolean }>) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors',
        active
          ? 'bg-accent-subtle text-accent-text'
          : 'text-secondary hover:bg-background-alt hover:text-foreground',
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden strokeWidth={2} />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

function DisabledItem({ item }: Readonly<{ item: NavItem }>) {
  const Icon = item.icon;
  return (
    <Tooltip content={`${item.label} is coming soon`} side="right">
      <span
        aria-disabled="true"
        className="text-subtle flex cursor-not-allowed items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium"
      >
        <Icon className="size-4 shrink-0" aria-hidden strokeWidth={2} />
        <span className="truncate">{item.label}</span>
        <span className="bg-neutral-bg text-2xs text-muted ml-auto rounded-full px-1.5 py-0.5 font-semibold tracking-wide uppercase">
          soon
        </span>
      </span>
    </Tooltip>
  );
}

export function SidebarNav({ className }: Readonly<{ className?: string }>) {
  const pathname = usePathname() ?? '';

  return (
    <nav aria-label="Primary" className={cn('flex flex-col gap-5', className)}>
      {NAV_GROUPS.map((group) => (
        <div key={group.title} className="flex flex-col gap-1">
          <p className="text-2xs text-muted px-2.5 font-semibold tracking-wide uppercase">
            {group.title}
          </p>
          <ul className="flex flex-col gap-0.5">
            {group.items.map((item) => (
              <li key={item.href}>
                {item.live ? (
                  <LiveItem item={item} active={isActive(pathname, item.href)} />
                ) : (
                  <DisabledItem item={item} />
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
