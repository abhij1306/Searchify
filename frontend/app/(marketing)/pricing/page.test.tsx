import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';

import { PRICING_TABLE_ROWS, PRICING_TIERS } from '@/lib/marketing-content/pricing';

import Page from './page';

// Plain render — no providers, no MSW: the pricing page is a sync RSC with no
// client islands of its own (the shared nav/footer chrome lives in the group
// layout and is covered by its own component tests + e2e).
describe('Pricing page (public marketing `/pricing`)', () => {
  it('renders exactly one h1 and keeps the product name out of h2-h6', () => {
    render(<Page />);

    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/pay for the evidence layer/i);

    // No h2-h6 may contain the product name (keeps heading queries unambiguous).
    for (const heading of screen.getAllByRole('heading')) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }
  });

  it('renders the four tier cards from the module, Pro as the popular card', () => {
    const { container } = render(<Page />);

    const cards = Array.from(container.querySelectorAll<HTMLElement>('.tier-card'));
    expect(cards).toHaveLength(4);

    const byName = (name: string) => {
      const card = cards.find((c) => c.querySelector('.tier-name')?.textContent === name);
      expect(card, `tier card "${name}"`).toBeDefined();
      return card as HTMLElement;
    };

    for (const tier of PRICING_TIERS) {
      const card = byName(tier.name);
      expect(
        within(card).getByRole('link', { name: new RegExp(tier.cta.label, 'i') }),
      ).toHaveAttribute('href', tier.cta.href);
    }

    // Prices render verbatim from the module — until the user fills them, the
    // Starter/Pro cards show the '[TODO(user)]' placeholder as their amount.
    expect(byName('Starter').querySelector('.amount')).toHaveTextContent('[TODO(user)]');
    expect(byName('Pro').querySelector('.amount')).toHaveTextContent('[TODO(user)]');
    expect(byName('Free sample').querySelector('.amount')).toHaveTextContent('$0');
    expect(byName('Enterprise').querySelector('.amount')).toHaveTextContent('Custom');

    // The highlighted tier is the featured card.
    expect(byName('Pro')).toHaveClass('popular');
  });

  it('renders the comparison table with the Pro column and the grounded dimensions', () => {
    render(<Page />);

    // Column headers come from the tier module; Pro is the highlighted column.
    for (const tier of PRICING_TIERS) {
      expect(screen.getByRole('columnheader', { name: tier.name })).toBeInTheDocument();
    }
    expect(screen.getByRole('columnheader', { name: 'Pro' })).toHaveClass('hl');

    // One header row + one body row per module dimension, each addressable.
    expect(screen.getAllByRole('row')).toHaveLength(1 + PRICING_TABLE_ROWS.length);
    for (const row of PRICING_TABLE_ROWS) {
      expect(screen.getByRole('rowheader', { name: row.dimension })).toBeInTheDocument();
    }

    // Spot-check grounded pro cells: full inventory rides every paid tier,
    // and the pro monitored-URL cell is still a visible placeholder.
    const inventoryRow = screen.getByRole('row', { name: /Site Health crawl mode/ });
    expect(within(inventoryRow).getAllByText('Full progressive inventory')).toHaveLength(3);
    const monitoredRow = screen.getByRole('row', { name: /Monitored URL set/ });
    expect(within(monitoredRow).getByText('[TODO(user)]')).toBeInTheDocument();
  });

  it('renders the BYOK trust strip', () => {
    render(<Page />);

    const strip = screen.getByRole('region', { name: /bring your own keys/i });
    expect(within(strip).getByText(/bring your own api keys/i)).toBeInTheDocument();
    expect(within(strip).getByText(/encrypted at rest/i)).toBeInTheDocument();
  });

  it('links the FAQ teaser to /faq', () => {
    render(<Page />);

    const teaser = screen.getByRole('region', { name: 'FAQ' });
    expect(within(teaser).getByRole('link', { name: /read the faq/i })).toHaveAttribute(
      'href',
      '/faq',
    );
  });

  it('closes with a CTA band linking /register plus the Enterprise contact placeholder', () => {
    render(<Page />);

    const finalCta = screen.getByRole('region', { name: 'Get started' });
    expect(within(finalCta).getByRole('link', { name: /get started/i })).toHaveAttribute(
      'href',
      '/register',
    );
    expect(within(finalCta).getByRole('link', { name: /enterprise contact/i })).toHaveAttribute(
      'href',
      '#',
    );
  });
});
