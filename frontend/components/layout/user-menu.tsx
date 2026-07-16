'use client';

import { useMutation } from '@tanstack/react-query';
import { LogOut } from 'lucide-react';

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
import { cn } from '@/lib/utils';

/** Avatar initials from an email local part. */
function emailInitials(email: string) {
  const local = email.split('@')[0] ?? email;
  return local.slice(0, 2).toUpperCase();
}

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
          'focus-ring flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left transition-colors hover:bg-background-alt',
          className,
        )}
      >
        <span
          aria-hidden
          className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-2xs font-bold uppercase text-accent-text"
        >
          {emailInitials(user.email)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">{user.email}</span>
          <span className="block truncate text-2xs capitalize text-muted">{user.role}</span>
        </span>
      </DropdownTrigger>
      <DropdownContent align="start" side="top" className="w-[calc(240px-2rem)]">
        <DropdownLabel>{user.email}</DropdownLabel>
        <DropdownSeparator className="my-1 h-px bg-border-subtle" />
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
