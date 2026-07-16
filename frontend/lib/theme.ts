export type ThemeMode = 'light' | 'dark';

/** localStorage key persisting the user's explicit theme choice. */
export const THEME_STORAGE_KEY = 'searchify-theme';

/** Attribute used to suppress transitions during a theme swap (see globals.css). */
export const THEME_TRANSITION_ATTR = 'data-theme-transition';

/**
 * Inline pre-hydration bootstrap: read the persisted theme (or the OS
 * preference) and set `data-theme` on <html> BEFORE React hydrates, so the
 * first paint never flashes the wrong theme. Injected verbatim into a
 * <script> in the root layout. Kept dependency-free and self-contained.
 */
export const THEME_BOOTSTRAP_SCRIPT = `(() => {
  try {
    var stored = localStorage.getItem('${THEME_STORAGE_KEY}');
    var dark = stored
      ? stored === 'dark'
      : window.matchMedia('(prefers-color-scheme:dark)').matches;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  } catch (e) {
    document.documentElement.dataset.theme = 'light';
  }
})();`;

/** Read the theme currently applied to <html> (defaults to light on the server). */
export function readTheme(): ThemeMode {
  if (typeof document === 'undefined') {
    return 'light';
  }
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

/** Apply + persist a theme, briefly suppressing transitions to avoid a flash. */
export function applyTheme(value: string | null | undefined) {
  if (typeof document === 'undefined') {
    return;
  }
  const nextTheme: ThemeMode = value === 'dark' ? 'dark' : 'light';
  const root = document.documentElement;
  root.setAttribute(THEME_TRANSITION_ATTR, 'true');
  root.dataset.theme = nextTheme;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
  } catch {
    /* storage unavailable (private mode / SSR) — theme still applied */
  }
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      root.removeAttribute(THEME_TRANSITION_ATTR);
    });
  });
}

/** Subscribe to theme changes (attribute mutations + cross-tab storage events). */
export function subscribeTheme(onStoreChange: () => void) {
  if (typeof window === 'undefined') {
    return () => undefined;
  }

  const observer = new MutationObserver(onStoreChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  });

  const storageHandler = (event: StorageEvent) => {
    if (event.key !== THEME_STORAGE_KEY) {
      return;
    }
    applyTheme(event.newValue ?? window.localStorage.getItem(THEME_STORAGE_KEY));
    onStoreChange();
  };

  window.addEventListener('storage', storageHandler);
  return () => {
    observer.disconnect();
    window.removeEventListener('storage', storageHandler);
  };
}
