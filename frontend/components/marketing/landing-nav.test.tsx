import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import { LandingNav } from './landing-nav';

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
    render(<LandingNav />);

    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();

    for (const [name, key] of [
      [/^product$/i, 'product'],
      [/^resources$/i, 'resources'],
      [/^solutions$/i, 'solutions'],
    ] as const) {
      const trigger = control(name, `drop-${key}`);
      expect(trigger).toHaveAttribute('aria-haspopup', 'true');
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
      const drop = panel(`drop-${key}`);
      expect(drop).toHaveAttribute('role', 'menu');
      expect(drop).toHaveClass('drop', `drop-${key}`);
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

  it('gives every dropdown item its own href (9 / 4 / 4 menuitems)', () => {
    render(<LandingNav />);

    // Product: the 6 feature rows (absolute anchors so they resolve from
    // subpages) + the "How it works" group's 3 numbered steps.
    const product = panel('drop-product');
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

    const resources = panel('drop-resources');
    expect(within(resources).getAllByRole('menuitem')).toHaveLength(4);
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
    // Documentation is the one external row: plain <a>, new tab, no referrer.
    const docs = within(resources).getByRole('menuitem', { name: /^documentation/i });
    expect(docs).toHaveAttribute('href', 'https://github.com/abhij1306/Searchify');
    expect(docs).toHaveAttribute('target', '_blank');
    expect(docs).toHaveAttribute('rel', 'noreferrer');

    const solutions = panel('drop-solutions');
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

  it('mirrors the drops as mobile accordions and closes the menu on item click', () => {
    render(<LandingNav />);

    for (const key of ['product', 'resources', 'solutions']) {
      expect(panel(`acc-${key}`)).toHaveClass('acc-body');
    }
    // The product accordion holds the merged set: 6 features + 3 steps.
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

  // The nav's visibility rules key entirely on these class tokens
  // (.mkt .nav-item.open .drop, .mkt .mobile-menu.open, .mkt .acc.open …),
  // so pin the React-state → class contract — aria alone can't catch a
  // mangled class list (jsdom never evaluates CSS selectors).
  it('drives the open-state classes the CSS keys on', () => {
    render(<LandingNav />);

    // Desktop dropdown: hovering the trigger's wrapper opens it.
    const product = control(/^product$/i, 'drop-product');
    const navItem = product.closest('.nav-item');
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
    fireEvent.click(product);
    expect(navItem).toHaveClass('nav-item', 'open');
    fireEvent.click(product);
    expect(navItem).toHaveClass('nav-item', 'open');

    // Mobile menu + an accordion per drop, each toggling its own open class.
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

    // Scrolled nav: the glass backdrop intensifies via `scrolled`.
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    Object.defineProperty(window, 'scrollY', { value: 50, configurable: true });
    fireEvent.scroll(window);
    expect(nav).toHaveClass('scrolled');
    Object.defineProperty(window, 'scrollY', { value: 0, configurable: true });
  });

  it('has exactly one theme toggle and working auth CTAs', () => {
    render(<LandingNav />);

    expect(screen.getAllByRole('button', { name: /toggle color theme/i })).toHaveLength(1);

    const signIns = screen.getAllByRole('link', { name: /sign in/i });
    expect(signIns.length).toBeGreaterThan(0);
    for (const link of signIns) expect(link).toHaveAttribute('href', '/login');

    const getStarteds = screen.getAllByRole('link', { name: /get started/i });
    expect(getStarteds.length).toBeGreaterThan(0);
    for (const link of getStarteds) expect(link).toHaveAttribute('href', '/register');
  });
});
