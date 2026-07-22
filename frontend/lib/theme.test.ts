import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  applyTheme,
  readTheme,
  subscribeTheme,
  THEME_BOOTSTRAP_SCRIPT,
  THEME_STORAGE_KEY,
} from '@/lib/theme';

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
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(calls).toBeGreaterThan(0);
    unsubscribe();
  });
});

describe('THEME_BOOTSTRAP_SCRIPT (pre-hydration, dark-first)', () => {
  // jsdom does not implement matchMedia — stub it so the suite pins that the
  // bootstrap's dark-first fallback ignores the OS preference entirely.
  const originalMatchMedia = window.matchMedia;

  const stubOsColorScheme = (scheme: 'light' | 'dark') => {
    window.matchMedia = ((query: string) => ({
      matches: query === `(prefers-color-scheme: ${scheme})`,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
  };

  // Evaluate the exact script string the root layout injects into <script>.
  const runBootstrap = () => {
    new Function(THEME_BOOTSTRAP_SCRIPT)();
  };

  beforeEach(() => {
    document.documentElement.removeAttribute('data-theme');
    window.localStorage.clear();
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    window.localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('falls back to dark with no stored choice, even on a light OS', () => {
    stubOsColorScheme('light');
    runBootstrap();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('falls back to dark with no stored choice on a dark OS', () => {
    stubOsColorScheme('dark');
    runBootstrap();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it("honors a stored 'light' choice", () => {
    stubOsColorScheme('dark');
    window.localStorage.setItem(THEME_STORAGE_KEY, 'light');
    runBootstrap();
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('falls back to dark when storage is unavailable', () => {
    stubOsColorScheme('light');
    const getItem = window.localStorage.getItem;
    Object.defineProperty(window.localStorage, 'getItem', {
      configurable: true,
      value: () => {
        throw new Error('storage unavailable');
      },
    });
    try {
      runBootstrap();
      expect(document.documentElement.dataset.theme).toBe('dark');
    } finally {
      Object.defineProperty(window.localStorage, 'getItem', {
        configurable: true,
        value: getItem,
      });
    }
  });
});
