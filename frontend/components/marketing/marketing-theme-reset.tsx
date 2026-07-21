'use client';

import { useEffect, useLayoutEffect } from 'react';

import { THEME_STORAGE_KEY } from '@/lib/theme';

// The reset must run synchronously on unmount (before the new route paints),
// not in a deferred passive effect — hence layout effect on the client.
const useIsomorphicLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/**
 * MarketingThemeReset — undoes the marketing dark-first default on the way
 * out. The (marketing) layout's inline script paints `data-theme='dark'` on
 * <html> when the visitor has no stored choice; on client-side navigation to
 * a non-marketing route that attribute would otherwise leak into app
 * surfaces (the shared bootstrap only re-runs on a full page load). On
 * unmount this restores exactly what the bootstrap would have chosen:
 * stored choice → OS preference → light.
 */
export function MarketingThemeReset() {
  useIsomorphicLayoutEffect(() => {
    return () => {
      try {
        const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
        const dark = stored
          ? stored === 'dark'
          : window.matchMedia('(prefers-color-scheme:dark)').matches;
        document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      } catch {
        document.documentElement.dataset.theme = 'light';
      }
    };
  }, []);
  return null;
}
