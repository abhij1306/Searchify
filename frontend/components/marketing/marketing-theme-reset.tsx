'use client';

import { useEffect, useLayoutEffect } from 'react';

import { THEME_STORAGE_KEY } from '@/lib/theme';

// The reset must run synchronously on unmount (before the new route paints),
// not in a deferred passive effect — hence layout effect on the client.
const useIsomorphicLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/**
 * MarketingThemeReset — handles the marketing dark-first default on mount and
 * restores the global bootstrap theme choice on unmount.
 *
 * On mount (such as client-side SPA navigation back to a marketing route),
 * if the visitor has no stored choice, it ensures `data-theme='dark'` is active.
 * On unmount (navigating away to app/auth routes), it restores the stored choice
 * or defaults to dark.
 */
export function MarketingThemeReset() {
  useIsomorphicLayoutEffect(() => {
    try {
      if (!window.localStorage.getItem(THEME_STORAGE_KEY)) {
        document.documentElement.dataset.theme = 'dark';
      }
    } catch {
      /* storage unavailable — leave the shared bootstrap choice alone */
    }

    return () => {
      try {
        const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
        document.documentElement.dataset.theme = stored === 'light' ? 'light' : 'dark';
      } catch {
        document.documentElement.dataset.theme = 'dark';
      }
    };
  }, []);
  return null;
}
