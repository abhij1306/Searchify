import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CompareDetailView } from '@/components/marketing/compare-detail';
import { COMPETITORS } from '@/lib/marketing-content/compare';

import Page from './page';

// Plain renders: the compare pages are sync RSC with no client islands, so
// no providers and no MSW. The async [competitor] route wrapper only resolves
// `params` and picks the module entry (covered by e2e's 200/404 cases) — the
// sync CompareDetailView it delegates to is rendered directly here.
describe('Compare index page (/compare)', () => {
  it('renders exactly one h1 and no h2–h6 containing the product name', () => {
    render(<Page />);

    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/how searchify compares/i);

    // Heading-name convention: only the h1 may contain "Searchify".
    for (const heading of screen.getAllByRole('heading')) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }
  });

  it('renders one card per competitor, each linking to its detail slug', () => {
    render(<Page />);

    const grid = screen.getByRole('region', { name: 'Competitors' });
    expect(within(grid).getAllByRole('link')).toHaveLength(COMPETITORS.length);
    expect(within(grid).getByText(`${COMPETITORS.length} comparisons`)).toBeInTheDocument();

    for (const competitor of COMPETITORS) {
      const card = within(grid).getByRole('link', { name: new RegExp(competitor.name) });
      expect(card).toHaveAttribute('href', `/compare/${competitor.slug}`);
      // Initial-letter tile stands in for a logo until assets exist.
      expect(within(card).getByText(competitor.name.charAt(0))).toBeInTheDocument();
      // Tagline renders verbatim from the module — '[TODO(user)]' until filled.
      expect(within(card).getByText(competitor.tagline)).toBeInTheDocument();
    }
  });

  it('closes with a CTA band linking to /register', () => {
    render(<Page />);

    const ctaBand = screen.getByRole('region', { name: 'Get started' });
    expect(within(ctaBand).getByRole('link', { name: /get started/i })).toHaveAttribute(
      'href',
      '/register',
    );
  });
});

describe('CompareDetailView (/compare/[competitor])', () => {
  const competitor = COMPETITORS[0];

  it('renders exactly one h1 and no h2–h6 containing the product name', () => {
    render(<CompareDetailView competitor={competitor} />);

    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(`Searchify vs ${competitor.name}.`);

    for (const heading of screen.getAllByRole('heading')) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }
  });

  it('shows the real Searchify column and visibly marked TODO competitor cells', () => {
    render(<CompareDetailView competitor={competitor} />);

    // Table header: real Searchify column, competitor column named for the slug.
    expect(screen.getByRole('columnheader', { name: 'Searchify' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: competitor.name })).toBeInTheDocument();

    // Searchify column: the docs-grounded strings render verbatim.
    for (const row of competitor.rows) {
      expect(screen.getByText(row.dimension)).toBeInTheDocument();
      expect(screen.getByText(row.searchify)).toBeInTheDocument();
    }

    // Every competitor cell stays visibly '[TODO(user)]' until first-party
    // research lands (one per row, plus the tagline chip and verdict slot).
    const todoMarks = screen.getAllByText('[TODO(user)]');
    expect(todoMarks.length).toBeGreaterThanOrEqual(competitor.rows.length);
  });

  it('shows the honest-framing line under the table', () => {
    render(<CompareDetailView competitor={competitor} />);

    expect(screen.getByText(/maintained by the Searchify team/i)).toBeInTheDocument();
    expect(screen.getByText(/pending first-party research/i)).toBeInTheDocument();
    expect(screen.getByText(/verify current competitor features/i)).toBeInTheDocument();
  });

  it('links back to the comparison index', () => {
    render(<CompareDetailView competitor={competitor} />);

    expect(screen.getByRole('link', { name: /all comparisons/i })).toHaveAttribute(
      'href',
      '/compare',
    );
  });
});
