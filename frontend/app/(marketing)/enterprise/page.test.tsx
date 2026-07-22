import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CONTACT_EMAIL } from '@/lib/marketing-content/social';

import Page from './page';

// Plain render — the page is a sync RSC with no client islands, so it needs
// no providers and no MSW.
const EXPECTED_CONTACT_HREF = CONTACT_EMAIL ? `mailto:${CONTACT_EMAIL}` : '/register';

describe('Enterprise page (public marketing `/enterprise`)', () => {
  it('renders exactly one h1 and no product-name subheadings', () => {
    render(<Page />);

    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/enterprise-grade evidence/i);

    // No h2-h6 may contain the product name (keeps heading queries unambiguous).
    for (const heading of screen.getAllByRole('heading')) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }
  });

  it('renders the trustworthy-operations grid grounded in the README', () => {
    render(<Page />);

    const ops = screen.getByRole('region', { name: 'Enterprise capabilities' });
    expect(within(ops).getAllByRole('heading', { level: 3 })).toHaveLength(4);
    // README "Built for trustworthy operations" bullets, rendered verbatim-ish.
    expect(within(ops).getByText(/UUID identifiers throughout/i)).toBeInTheDocument();
    expect(within(ops).getByText(/Immutable artifacts/i)).toBeInTheDocument();
    expect(within(ops).getByText(/FOR UPDATE SKIP LOCKED/i)).toBeInTheDocument();
    expect(
      within(ops).getByText(/backend topology never reaches the client bundle/i),
    ).toBeInTheDocument();
    expect(within(ops).getByText(/Zod \+ Pydantic/i)).toBeInTheDocument();
  });

  it('renders no GitHub/MIT links, keeps the self-host card, and points the hero ghost at /pricing', () => {
    render(<Page />);

    // The repo is private — no GitHub or MIT-license links anywhere.
    expect(screen.queryByRole('link', { name: /github|MIT license/i })).toBeNull();

    // The self-host deployment card still renders.
    const deploy = screen.getByRole('region', { name: 'Deployment options' });
    expect(within(deploy).getByRole('heading', { name: 'Self-hosted' })).toBeInTheDocument();

    // The hero ghost CTA now routes to the pricing page.
    expect(screen.getByRole('link', { name: /compare plans/i })).toHaveAttribute(
      'href',
      '/pricing',
    );
  });

  it('shows custom values for every enterprise limit', () => {
    render(<Page />);

    const limits = screen.getByRole('region', { name: 'Custom limits' });
    for (const label of ['Monthly audit runs', 'Monitored URLs', 'Seats', 'Evidence retention']) {
      expect(within(limits).getByText(label)).toBeInTheDocument();
    }
    expect(within(limits).getAllByText('Custom')).toHaveLength(6);
  });

  it('renders the contact CTA with a real destination, never href="#"', () => {
    render(<Page />);

    const cta = screen.getByRole('region', { name: 'Contact sales' });
    const contacts = screen.getAllByRole('link', { name: /contact sales/i });
    expect(contacts.length).toBeGreaterThan(0);
    for (const contact of contacts) {
      expect(contact).toHaveAttribute('href', EXPECTED_CONTACT_HREF);
      // The CTA must never degrade to a dead anchor.
      expect(contact).not.toHaveAttribute('href', '#');
    }
    expect(within(cta).getByRole('link', { name: /contact sales/i })).toBeInTheDocument();
  });
});
