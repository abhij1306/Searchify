import { describe, expect, it } from 'vitest';

import { cn, emailInitials } from './utils';

describe('cn', () => {
  it('merges conditional class names and resolves Tailwind conflicts', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4');
    expect(cn('text-sm', false && 'hidden', 'font-bold')).toBe('text-sm font-bold');
  });
});

describe('emailInitials', () => {
  it('takes the first two characters of the local part, upper-cased', () => {
    expect(emailInitials('abhineet.jain@cube27.com')).toBe('AB');
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
