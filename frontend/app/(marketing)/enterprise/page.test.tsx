import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CONTACT_EMAIL, GITHUB_URL, LICENSE_URL } from '@/lib/marketing-content/social';

import Page from './page';

// Plain render — the page is a sync RSC with no client islands, so it needs
// no providers and no MSW.
const EXPECTED_CONTACT_HREF = CONTACT_EMAIL ? `mailto:${CONTACT_EMAIL}` : '#';

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

  it('links the MIT license and GitHub source with real hrefs', () => {
    render(<Page />);

    expect(screen.getByRole('link', { name: 'Full source on GitHub' })).toHaveAttribute(
      'href',
      GITHUB_URL,
    );
    expect(screen.getByRole('link', { name: 'MIT license' })).toHaveAttribute('href', LICENSE_URL);
    expect(screen.getByRole('link', { name: /view the codebase/i })).toHaveAttribute(
      'href',
      GITHUB_URL,
    );
  });

  it('shows [TODO(user)] for every custom-limit value', () => {
    render(<Page />);

    const limits = screen.getByRole('region', { name: 'Custom limits' });
    for (const label of ['Monthly audit runs', 'Monitored URLs', 'Seats', 'Evidence retention']) {
      expect(within(limits).getByText(label)).toBeInTheDocument();
    }
    // Volumes, seats, retention — all six dials are user-fillable placeholders.
    expect(within(limits).getAllByText('[TODO(user)]')).toHaveLength(6);
  });

  it('renders the contact CTA with the mailto-or-placeholder href', () => {
    render(<Page />);

    const cta = screen.getByRole('region', { name: 'Contact sales' });
    const contacts = screen.getAllByRole('link', { name: /contact sales/i });
    expect(contacts.length).toBeGreaterThan(0);
    for (const contact of contacts) {
      expect(contact).toHaveAttribute('href', EXPECTED_CONTACT_HREF);
    }
    expect(within(cta).getByRole('link', { name: /contact sales/i })).toBeInTheDocument();
  });
});
