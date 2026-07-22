import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import { LandingNav } from './landing-nav';

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  mswServer.use(
    http.get('/api/v1/auth/me', () =>
      HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
    ),
    http.get('/api/v1/projects', () => HttpResponse.json([])),
  );
});
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

/**
 * The desktop trigger and the mobile accordion head share a name, so pick a
 * button by the panel it controls (`drop-*` desktop, `acc-*` mobile).
 */
function control(name: RegExp, controlsId: string): HTMLElement {
  const button = screen
    .getAllByRole('button', { name })
    .find((candidate) => candidate.getAttribute('aria-controls') === controlsId);
  expect(button, `expected a button controlling #${controlsId}`).toBeDefined();
  return button as HTMLElement;
}

function panel(id: string): HTMLElement {
  const el = document.getElementById(id);
  expect(el, `expected #${id}`).not.toBeNull();
  return el as HTMLElement;
}

describe('LandingNav', () => {
  it('renders three dropdown triggers with the menu contract, plus plain Enterprise/Pricing links', () => {
    renderWithProviders(<LandingNav />);

    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();

    for (const [name] of [
      [/^product$/i, 'product'],
      [/^resources$/i, 'resources'],
      [/^solutions$/i, 'solutions'],
    ] as const) {
      const trigger = control(name, 'desktop-nav-panel');
      expect(trigger).toHaveAttribute('aria-haspopup', 'true');
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
      const drop = panel('desktop-nav-panel');
      expect(drop).toHaveAttribute('role', 'menu');
      expect(drop).toHaveClass('drop', 'shared-drop');
    }

    // Enterprise / Pricing are plain links — no dropdown chrome whatsoever.
    for (const [name, href] of [
      [/^enterprise$/i, '/enterprise'],
      [/^pricing$/i, '/pricing'],
    ] as const) {
      const links = screen.getAllByRole('link', { name });
      expect(links.length).toBeGreaterThan(0);
      for (const link of links) {
        expect(link).not.toHaveAttribute('aria-haspopup');
        expect(link).not.toHaveAttribute('aria-expanded');
      }
      const desktop = links.find((link) => link.classList.contains('nav-link'));
      expect(desktop, `expected a desktop .nav-link for ${href}`).toBeDefined();
      expect(desktop).toHaveAttribute('href', href);
    }

    // The old #evidence anchor link is gone from the nav (the footer keeps it).
    expect(screen.queryByRole('link', { name: /^evidence$/i })).not.toBeInTheDocument();
  });

  it('gives every dropdown item its own href (9 / 3 / 4 menuitems)', () => {
    renderWithProviders(<LandingNav />);

    // Product: the 6 feature rows (absolute anchors so they resolve from
    // subpages) + the "How it works" group's 3 numbered steps.
    fireEvent.mouseEnter(
      control(/^product$/i, 'desktop-nav-panel').closest('.nav-item') as Element,
    );
    const product = panel('desktop-nav-panel');
    expect(within(product).getAllByRole('menuitem')).toHaveLength(9);
    for (const title of [
      'Three-engine coverage',
      'Deterministic scoring',
      'Evidence explorer',
      'Competitor benchmarking',
      'BYOK privacy',
      'Repeatable trends',
    ]) {
      expect(
        within(product).getByRole('menuitem', { name: new RegExp(title, 'i') }),
      ).toHaveAttribute('href', '/#features');
    }
    for (const title of ['Define your workspace', 'Run the audit', 'Read the evidence']) {
      expect(
        within(product).getByRole('menuitem', { name: new RegExp(title, 'i') }),
      ).toHaveAttribute('href', '/#how-it-works');
    }
    expect(product.querySelector('.d-group-label')).toHaveTextContent('How it works');

    fireEvent.mouseEnter(
      control(/^resources$/i, 'desktop-nav-panel').closest('.nav-item') as Element,
    );
    const resources = panel('desktop-nav-panel');
    expect(within(resources).getAllByRole('menuitem')).toHaveLength(3);
    expect(within(resources).getByRole('menuitem', { name: /^blog/i })).toHaveAttribute(
      'href',
      '/blog',
    );
    expect(within(resources).getByRole('menuitem', { name: /^faq/i })).toHaveAttribute(
      'href',
      '/faq',
    );
    expect(within(resources).getByRole('menuitem', { name: /^compare/i })).toHaveAttribute(
      'href',
      '/compare',
    );
    expect(within(resources).queryByRole('menuitem', { name: /^documentation/i })).toBeNull();

    fireEvent.mouseEnter(
      control(/^solutions$/i, 'desktop-nav-panel').closest('.nav-item') as Element,
    );
    const solutions = panel('desktop-nav-panel');
    expect(within(solutions).getAllByRole('menuitem')).toHaveLength(4);
    expect(within(solutions).getByRole('menuitem', { name: /^agencies/i })).toHaveAttribute(
      'href',
      '/solutions#agencies',
    );
    expect(within(solutions).getByRole('menuitem', { name: /in-house teams/i })).toHaveAttribute(
      'href',
      '/solutions#in-house',
    );
    expect(within(solutions).getByRole('menuitem', { name: /^founders/i })).toHaveAttribute(
      'href',
      '/solutions#founders',
    );
    expect(within(solutions).getByRole('menuitem', { name: /pr & comms/i })).toHaveAttribute(
      'href',
      '/solutions#pr',
    );
  });

  it('keeps every nav link inside the site', () => {
    const { container } = renderWithProviders(<LandingNav />);

    const links = Array.from(container.querySelectorAll('a[href]'));
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link.getAttribute('href')).toMatch(/^\//);
    }
    expect(container.querySelector('a[target="_blank"]')).toBeNull();
  });

  it('mirrors the drops as mobile accordions and closes the menu on item click', () => {
    renderWithProviders(<LandingNav />);

    for (const key of ['product', 'resources', 'solutions']) {
      expect(panel(`acc-${key}`)).toHaveClass('acc-body');
    }
    expect(within(panel('acc-product')).getAllByRole('link')).toHaveLength(9);

    fireEvent.click(screen.getByRole('button', { name: /open menu/i }));
    const menu = panel('mobile-menu');
    expect(menu).toHaveClass('mobile-menu', 'open');

    fireEvent.click(control(/^product$/i, 'acc-product'));
    const item = within(panel('acc-product')).getByRole('link', {
      name: /three-engine coverage/i,
    });
    expect(item).toHaveAttribute('href', '/#features');
    fireEvent.click(item);
    expect(menu).toHaveClass('mobile-menu');
    expect(menu).not.toHaveClass('open');
  });

  it('drives the open-state classes the CSS keys on', () => {
    renderWithProviders(<LandingNav />);

    const product = control(/^product$/i, 'desktop-nav-panel');
    const navItem = product.closest('.nav-item');
    expect(navItem).not.toBeNull();
    fireEvent.mouseEnter(navItem as Element);
    expect(navItem).toHaveClass('nav-item', 'open');
    expect(product).toHaveAttribute('aria-expanded', 'true');

    fireEvent.mouseLeave(navItem as Element);
    expect(navItem).toHaveClass('nav-item', 'open');

    fireEvent.click(product);
    expect(navItem).toHaveClass('nav-item', 'open');
    fireEvent.click(product);
    expect(navItem).toHaveClass('nav-item', 'open');

    fireEvent.click(screen.getByRole('button', { name: /open menu/i }));
    expect(document.getElementById('mobile-menu')).toHaveClass('mobile-menu', 'open');
    for (const [name, key] of [
      [/^product$/i, 'acc-product'],
      [/^resources$/i, 'acc-resources'],
      [/^solutions$/i, 'acc-solutions'],
    ] as const) {
      const accHead = control(name, key);
      fireEvent.click(accHead);
      expect(accHead.closest('.acc')).toHaveClass('acc', 'open');
      fireEvent.click(accHead);
      expect(accHead.closest('.acc')).toHaveClass('acc');
      expect(accHead.closest('.acc')).not.toHaveClass('open');
    }

    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    Object.defineProperty(window, 'scrollY', { value: 50, configurable: true });
    fireEvent.scroll(window);
    expect(nav).toHaveClass('scrolled');
    Object.defineProperty(window, 'scrollY', { value: 0, configurable: true });
  });

  it('has exactly one theme toggle and working auth CTAs for signed-out users', () => {
    mswServer.use(
      http.get('/api/v1/auth/me', () =>
        HttpResponse.json({ detail: 'Unauthorized' }, { status: 401 }),
      ),
    );

    renderWithProviders(<LandingNav />);

    expect(screen.getAllByRole('button', { name: /toggle color theme/i })).toHaveLength(1);

    const signIns = screen.getAllByRole('link', { name: /sign in/i });
    expect(signIns.length).toBeGreaterThan(0);
    for (const link of signIns) expect(link).toHaveAttribute('href', '/login');

    const getStarteds = screen.getAllByRole('link', { name: /get started/i });
    expect(getStarteds.length).toBeGreaterThan(0);
    for (const link of getStarteds) expect(link).toHaveAttribute('href', '/register');
  });

  it('renders a Dashboard CTA linking to /visibility when signed in', async () => {
    const sessionUser = {
      id: '22222222-2222-4222-8222-222222222222',
      email: 'nav@example.com',
      role: 'owner',
      is_active: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    const proj = {
      id: '11111111-1111-4111-8111-111111111111',
      workspace_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      name: 'Acme',
      brand_name: 'Acme',
      website_url: 'https://example.com',
      country_code: 'US',
      language_code: 'en',
      benchmark_mode: 'consumer_like',
      default_repetitions: 3,
      brand: { aliases: [] },
      owned_domains: [],
      unintended_domains: [],
      competitors: [],
      prompt_sets: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };

    mswServer.use(
      http.get('/api/v1/auth/me', () => HttpResponse.json({ user: sessionUser })),
      http.get('/api/v1/projects', () => HttpResponse.json([proj])),
    );

    renderWithProviders(<LandingNav />);

    await waitFor(() => {
      const dashboardLinks = screen.getAllByRole('link', { name: /dashboard/i });
      expect(dashboardLinks.length).toBeGreaterThan(0);
      for (const link of dashboardLinks) {
        expect(link).toHaveAttribute('href', '/visibility');
      }
    });

    expect(screen.queryByRole('link', { name: /sign in/i })).toBeNull();
    expect(screen.queryByRole('link', { name: /get started/i })).toBeNull();
  });
});
