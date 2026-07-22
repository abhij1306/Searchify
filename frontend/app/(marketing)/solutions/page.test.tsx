import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import Page from './page';

// Plain render — the solutions page has no client islands (no providers, no
// MSW). The shared chrome (nav/footer) lives in the route-group layout and is
// covered by colocated component tests + e2e.
describe('Solutions page (public marketing `/solutions`)', () => {
  it('renders exactly one h1 and keeps the product name out of h2-h6', () => {
    render(<Page />);

    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/every team behind the brand/i);

    // No h2-h6 may contain the product name (keeps heading queries unambiguous).
    const headings = screen.getAllByRole('heading');
    for (const heading of headings) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }
  });

  it('exposes the four segment anchors the nav Solutions dropdown targets', () => {
    const { container } = render(<Page />);

    // The nav dropdown links to `/solutions#<id>` — pin the ids.
    for (const hash of ['#agencies', '#in-house', '#founders', '#pr']) {
      expect(container.querySelector(hash)).not.toBeNull();
    }

    // The hero chip nav points at the same in-page anchors.
    const segNav = screen.getByRole('navigation', { name: 'Solutions by team' });
    for (const hash of ['#agencies', '#in-house', '#founders', '#pr']) {
      const chips = within(segNav).getAllByRole('link');
      expect(chips.some((chip) => chip.getAttribute('href') === hash)).toBe(true);
    }
  });

  it('renders each segment with its key feature mappings and a /register CTA', () => {
    render(<Page />);

    const agencies = screen.getByRole('region', { name: 'For agencies' });
    expect(within(agencies).getByText(/multi-project workspaces/i)).toBeInTheDocument();
    expect(
      within(agencies).getByText(/authenticated CSV \+ Markdown downloads/i),
    ).toBeInTheDocument();
    expect(
      within(agencies).getByRole('link', { name: /start a client workspace/i }),
    ).toHaveAttribute('href', '/register');

    const inHouse = screen.getByRole('region', { name: 'For in-house teams' });
    expect(within(inHouse).getByText(/cross-run trends/i)).toBeInTheDocument();
    expect(within(inHouse).getByText(/Site Health \+ AEO scores/i)).toBeInTheDocument();
    expect(within(inHouse).getByRole('link', { name: /start monitoring/i })).toHaveAttribute(
      'href',
      '/register',
    );

    const founders = screen.getByRole('region', { name: 'For founders' });
    expect(within(founders).getByText(/free sample Site Health crawl/i)).toBeInTheDocument();
    expect(within(founders).getByText(/self-host when you outgrow the cloud/i)).toBeInTheDocument();
    expect(within(founders).getByRole('link', { name: /run a free sample/i })).toHaveAttribute(
      'href',
      '/register',
    );

    const pr = screen.getByRole('region', { name: 'For PR and communications' });
    expect(within(pr).getByText(/mention \+ citation tracking/i)).toBeInTheDocument();
    expect(within(pr).getByText(/query-fanout evidence/i)).toBeInTheDocument();
    expect(within(pr).getByRole('link', { name: /track your narrative/i })).toHaveAttribute(
      'href',
      '/register',
    );
  });

  it('closes with a CTA band linking to /register and /pricing', () => {
    render(<Page />);

    const finalCta = screen.getByRole('region', { name: 'Get started' });
    expect(within(finalCta).getByRole('link', { name: /get started/i })).toHaveAttribute(
      'href',
      '/register',
    );
    expect(within(finalCta).getByRole('link', { name: /see pricing/i })).toHaveAttribute(
      'href',
      '/pricing',
    );
  });
});
