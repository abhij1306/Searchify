import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import { queryKeys } from '@/lib/api/query-keys';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, push: vi.fn(), refresh: vi.fn() }),
}));

import Page from './page';

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  mswServer.resetHandlers();
  replace.mockReset();
});
afterAll(() => mswServer.close());

/** Anonymous visitor: the session check 401s and the island stays inert. */
function stubAnonymous() {
  mswServer.use(
    http.get('/api/v1/auth/me', () =>
      HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
    ),
  );
}

describe('Landing page (public marketing `/`)', () => {
  it('renders exactly one h1 and keeps the marketing content up after the 401 settles', async () => {
    stubAnonymous();
    const { queryClient } = renderWithProviders(<Page />);

    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/talk about your brand/i);

    // No h2-h6 may contain the product name (keeps heading queries unambiguous).
    const headings = screen.getAllByRole('heading');
    for (const heading of headings) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }

    await waitFor(() =>
      expect(queryClient.getQueryState(queryKeys.auth.me())?.status).toBe('error'),
    );
    expect(replace).not.toHaveBeenCalled();
    expect(h1s[0]).toBeInTheDocument();
  });

  it('exposes nav landmarks, dropdown triggers, and anchor targets', () => {
    stubAnonymous();
    const { container } = renderWithProviders(<Page />);

    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();

    // Hover-open dropdown triggers carry the menu contract (the desktop
    // trigger owns `aria-controls`; the mobile accordion repeats the name).
    const product = screen
      .getAllByRole('button', { name: /^product$/i })
      .find((button) => button.getAttribute('aria-controls') === 'drop-product');
    expect(product).toBeDefined();
    expect(product).toHaveAttribute('aria-haspopup', 'true');
    expect(product).toHaveAttribute('aria-expanded', 'false');
    const how = screen
      .getAllByRole('button', { name: /how it works/i })
      .find((button) => button.getAttribute('aria-controls') === 'drop-how');
    expect(how).toBeDefined();
    expect(how).toHaveAttribute('aria-expanded', 'false');

    // Dropdown panels intentionally duplicate these anchors → getAllByRole.
    for (const hash of ['#features', '#how-it-works', '#evidence']) {
      const links = screen
        .getAllByRole('link')
        .filter((link) => link.getAttribute('href') === hash);
      expect(links.length, `expected at least one ${hash} link`).toBeGreaterThan(0);
      expect(container.querySelector(hash)).not.toBeNull();
    }
  });

  // The nav's visibility rules key entirely on these class tokens
  // (.mkt .nav-item.open .drop, .mkt .mobile-menu.open, .mkt .acc.open …),
  // so pin the React-state → class contract — aria alone can't catch a
  // mangled class list (jsdom never evaluates CSS selectors).
  it('drives the open-state classes the CSS keys on', () => {
    stubAnonymous();
    renderWithProviders(<Page />);

    // Desktop dropdown: hovering the trigger's wrapper opens it.
    const product = screen
      .getAllByRole('button', { name: /^product$/i })
      .find((button) => button.getAttribute('aria-controls') === 'drop-product');
    const navItem = product?.closest('.nav-item');
    expect(navItem).not.toBeNull();
    fireEvent.mouseEnter(navItem as Element);
    expect(navItem).toHaveClass('nav-item', 'open');
    expect(product).toHaveAttribute('aria-expanded', 'true');
    fireEvent.mouseLeave(navItem as Element);
    expect(navItem).toHaveClass('nav-item');
    expect(navItem).not.toHaveClass('open');

    // Trigger click opens (touch / Enter-Space path; fireEvent.click
    // dispatches no focus, so onFocusCapture can't interfere). Click only
    // ever opens — it must not close a panel hover/focus may have opened.
    fireEvent.click(product as Element);
    expect(navItem).toHaveClass('nav-item', 'open');
    fireEvent.click(product as Element);
    expect(navItem).toHaveClass('nav-item', 'open');

    // Mobile menu + accordion.
    fireEvent.click(screen.getByRole('button', { name: /open menu/i }));
    expect(document.getElementById('mobile-menu')).toHaveClass('mobile-menu', 'open');
    const accProduct = screen
      .getAllByRole('button', { name: /^product$/i })
      .find((button) => button.getAttribute('aria-controls') === 'acc-product');
    fireEvent.click(accProduct as Element);
    expect(accProduct?.closest('.acc')).toHaveClass('acc', 'open');

    // Scrolled nav: the glass backdrop intensifies via `scrolled`.
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    Object.defineProperty(window, 'scrollY', { value: 50, configurable: true });
    fireEvent.scroll(window);
    expect(nav).toHaveClass('scrolled');
    Object.defineProperty(window, 'scrollY', { value: 0, configurable: true });
  });

  it('has exactly one theme toggle and working auth CTAs', () => {
    stubAnonymous();
    renderWithProviders(<Page />);

    expect(screen.getAllByRole('button', { name: /toggle color theme/i })).toHaveLength(1);

    const signIns = screen.getAllByRole('link', { name: /sign in/i });
    expect(signIns.length).toBeGreaterThan(0);
    for (const link of signIns) expect(link).toHaveAttribute('href', '/login');

    const getStarteds = screen.getAllByRole('link', { name: /get started/i });
    expect(getStarteds.length).toBeGreaterThan(0);
    for (const link of getStarteds) expect(link).toHaveAttribute('href', '/register');
  });
});
