import { describe, expect, it } from 'vitest';

import { cn, emailInitials } from './utils';

describe('cn', () => {
  it('merges conditional class names and resolves Tailwind conflicts', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4');
  });

  it('drops falsy conditional class names', () => {
    const isHidden = false;
    expect(cn('text-sm', isHidden && 'hidden', 'font-bold')).toBe('text-sm font-bold');
  });
});

describe('emailInitials', () => {
  it('takes the first two characters of the local part, upper-cased', () => {
    expect(emailInitials('test.user@example.test')).toBe('TE');
    expect(emailInitials('jo@example.com')).toBe('JO');
  });

  it('uses a single-character local part as-is', () => {
    expect(emailInitials('a@example.com')).toBe('A');
  });

  it('falls back to the raw value when there is no @', () => {
    expect(emailInitials('root')).toBe('RO');
  });

  it('returns an empty string for an empty input', () => {
    expect(emailInitials('')).toBe('');
  });
});
