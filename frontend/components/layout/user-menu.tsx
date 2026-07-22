'use client';

import { useMutation } from '@tanstack/react-query';
import { LogOut, Settings } from 'lucide-react';
import Link from 'next/link';

import {
  Dropdown,
  DropdownContent,
  DropdownItem,
  DropdownLabel,
  DropdownSeparator,
  DropdownTrigger,
} from '@/components/ui/dropdown';
import { authApi } from '@/lib/api/auth';
import { useSession } from '@/lib/auth/session-guard';
import { cn, emailInitials } from '@/lib/utils';

/**
 * UserMenu (F5) — shows the current user (from F4's `useSession`) and a logout
 * action. Logout posts `/auth/logout`, then clears the session cache and
 * redirects to `/login` via the guard's `clearSession` (regardless of the
 * network result — the cookie is cleared server-side and a stale client cache
 * must never strand the user).
 */
export function UserMenu({ className }: Readonly<{ className?: string }>) {
  const { user, clearSession } = useSession();

  const logout = useMutation({
    mutationFn: () => authApi.logout(),
    onSettled: () => clearSession(),
  });

  return (
    <Dropdown>
      <DropdownTrigger
        className={cn(
          'focus-ring hover:bg-background-alt flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left transition-colors',
          className,
        )}
      >
        <span
          aria-hidden
          className="bg-accent-soft text-2xs text-accent-text flex size-7 shrink-0 items-center justify-center rounded-full font-bold uppercase"
        >
          {emailInitials(user.email)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="text-foreground block truncate text-sm font-medium">{user.email}</span>
          <span className="text-2xs text-muted block truncate capitalize">{user.role}</span>
        </span>
      </DropdownTrigger>
      <DropdownContent align="start" side="top" className="w-[calc(240px-2rem)]">
        <DropdownLabel>{user.email}</DropdownLabel>
        <DropdownSeparator className="bg-border-subtle my-1 h-px" />
        <DropdownItem asChild>
          <Link href="/settings">
            <Settings className="size-4 shrink-0" aria-hidden />
            <span>Settings</span>
          </Link>
        </DropdownItem>
        <DropdownItem
          onSelect={(event) => {
            event.preventDefault();
            logout.mutate();
          }}
          disabled={logout.isPending}
        >
          <LogOut className="size-4 shrink-0" aria-hidden />
          <span>{logout.isPending ? 'Signing out…' : 'Sign out'}</span>
        </DropdownItem>
      </DropdownContent>
    </Dropdown>
  );
}
