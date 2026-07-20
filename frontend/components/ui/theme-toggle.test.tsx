import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ThemeToggle } from '@/components/ui/theme-toggle';
import { THEME_STORAGE_KEY } from '@/lib/theme';

describe('ThemeToggle', () => {
  beforeEach(() => {
    document.documentElement.dataset.theme = 'light';
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it('toggles data-theme from light to dark and persists it', async () => {
    render(<ThemeToggle />);
    const button = screen.getByRole('button', { name: /toggle color theme/i });

    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');

    // The toggle reads theme via an async MutationObserver; wait for the
    // internal store to reflect "dark" before toggling back.
    await waitFor(() => expect(button.getAttribute('title')).toMatch(/switch to light mode/i));

    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });
});
