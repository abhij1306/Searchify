import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { applyTheme, readTheme, subscribeTheme, THEME_STORAGE_KEY } from '@/lib/theme';

describe('theme', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.removeAttribute('data-theme-transition');
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it('readTheme defaults to light when unset', () => {
    expect(readTheme()).toBe('light');
  });

  it('applyTheme sets data-theme on <html>', () => {
    applyTheme('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(readTheme()).toBe('dark');
  });

  it('applyTheme persists the choice to localStorage', () => {
    applyTheme('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    applyTheme('light');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });

  it('applyTheme normalizes any non-dark value to light', () => {
    applyTheme('nonsense');
    expect(readTheme()).toBe('light');
    applyTheme(null);
    expect(readTheme()).toBe('light');
  });

  it('subscribe fires when data-theme mutates and can be unsubscribed', async () => {
    let calls = 0;
    const unsubscribe = subscribeTheme(() => {
      calls += 1;
    });
    applyTheme('dark');
    // MutationObserver callbacks are microtask/async — wait a tick.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(calls).toBeGreaterThan(0);
    unsubscribe();
  });
});
