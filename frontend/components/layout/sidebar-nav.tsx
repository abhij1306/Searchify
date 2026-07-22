'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { cn } from '@/lib/utils';

import { NAV_GROUPS, type NavItem } from './nav-items';
import { eyebrowClasses } from '@/components/ui/eyebrow';

/**
 * SidebarNav (F5) — grouped sidebar navigation (docs/design.md §9.2).
 *
 * Group labels are uppercase mono eyebrows (the midnight panel-label
 * pattern). All items are live `<Link>`s with the mockup's pill active state
 * (accent-soft bg + accent-text, icon inherits). Highlighting matches the
 * current route or any nested route (e.g. `/runs/[id]` highlights Runs).
 */
function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLink({ item, active }: Readonly<{ item: NavItem; active: boolean }>) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors',
        active
          ? 'bg-accent-soft text-accent-text'
          : 'text-secondary hover:bg-accent-soft/50 hover:text-foreground',
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden strokeWidth={2} />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

export function SidebarNav({ className }: Readonly<{ className?: string }>) {
  const pathname = usePathname() ?? '';

  return (
    <nav aria-label="Primary" className={cn('flex flex-col gap-5', className)}>
      {NAV_GROUPS.map((group) => (
        <div key={group.title} className="flex flex-col gap-1">
          <p className={cn(eyebrowClasses, 'px-2.5')}>{group.title}</p>
          <ul className="flex flex-col gap-0.5">
            {group.items.map((item) => (
              <li key={item.href}>
                <NavLink item={item} active={isActive(pathname, item.href)} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
