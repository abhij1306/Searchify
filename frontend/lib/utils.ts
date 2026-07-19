import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge conditional class names, resolving Tailwind conflicts. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Two-letter avatar initials from an email address, taken from the local part
 * (before the `@`) and upper-cased. Falls back to the raw value when there is
 * no `@`, and yields `''` for an empty string. Shared by the sidebar user menu
 * and the Settings account card so both avatars stay in sync.
 */
export function emailInitials(email: string) {
  const local = email.split('@')[0] ?? email;
  return local.slice(0, 2).toUpperCase();
}
