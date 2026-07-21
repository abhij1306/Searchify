import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { FAQ_GROUPS } from '@/lib/marketing-content/faq';
import { GITHUB_URL } from '@/lib/marketing-content/social';

import Page from './page';

const TOTAL_ITEMS = FAQ_GROUPS.reduce((sum, group) => sum + group.items.length, 0);

// Plain render — the page is a sync RSC with no client islands, so it needs
// no providers and no MSW. The shared chrome (nav/footer) lives in the
// (marketing) route-group layout and is covered by colocated component tests.
describe('FAQ page (public marketing `/faq`)', () => {
  it('renders exactly one h1 and keeps the product name out of h2–h6', () => {
    render(<Page />);

    const h1s = screen.getAllByRole('heading', { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(/frequently asked questions/i);

    // No h2-h6 may contain the product name (keeps heading queries unambiguous).
    for (const heading of screen.getAllByRole('heading')) {
      if (heading === h1s[0]) continue;
      expect(heading).not.toHaveTextContent(/searchify/i);
    }
  });

  it('renders the five module groups as labelled sections with h2 headings', () => {
    render(<Page />);

    expect(FAQ_GROUPS).toHaveLength(5);
    for (const group of FAQ_GROUPS) {
      const section = screen.getByRole('region', { name: group.heading });
      expect(within(section).getByRole('heading', { level: 2 })).toHaveTextContent(group.heading);
      expect(within(section).getByText(`${group.items.length} answers`)).toBeInTheDocument();
    }
  });

  it('renders the group rail with an anchor link + item count per group', () => {
    const { container } = render(<Page />);

    const toc = screen.getByRole('navigation', { name: 'FAQ groups' });
    const links = within(toc).getAllByRole('link');
    expect(links).toHaveLength(FAQ_GROUPS.length);

    for (const [index, group] of FAQ_GROUPS.entries()) {
      // Accessible name is "<heading> <count>", e.g. "Privacy & keys 3".
      expect(links[index]).toHaveTextContent(`${group.heading} ${group.items.length}`);
      // Each rail anchor resolves to the matching group section on the page.
      const href = links[index].getAttribute('href');
      expect(href).toMatch(/^#faq-/);
      if (!href) throw new Error('rail link missing href');
      expect(container.querySelector(href)).not.toBeNull();
    }
  });

  it('renders every module item as a native details/summary accordion row', () => {
    const { container } = render(<Page />);

    // One <details>/<summary> pair per module item — zero client JS.
    expect(container.querySelectorAll('details.faq-item')).toHaveLength(TOTAL_ITEMS);
    expect(container.querySelectorAll('summary.faq-q')).toHaveLength(TOTAL_ITEMS);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();

    // Every question and answer string from the module is rendered. Answers
    // may be split into inline links/todo-tag pills, so match on the full
    // textContent of the answer paragraph.
    for (const group of FAQ_GROUPS) {
      for (const item of group.items) {
        expect(screen.getByText(item.q)).toBeInTheDocument();
        expect(
          screen.getAllByText((_, el) => el?.tagName === 'P' && el.textContent === item.a).length,
        ).toBeGreaterThan(0);
      }
    }
  });

  it('keeps the unfilled billing answers as visible [TODO(user)] placeholders', () => {
    render(<Page />);

    const billing = screen.getByRole('region', { name: 'Account & billing' });
    // Every billing placeholder renders as the todo-tag pill: the two
    // bare-placeholder answers (refund, invoice) plus the inline one in the
    // cost answer.
    expect(within(billing).getAllByText('[TODO(user)]')).toHaveLength(3);
    expect(within(billing).getByText(/Hosted plans:/)).toHaveTextContent('[TODO(user)]');
  });

  it('links the real GitHub URL from the open-source group', () => {
    render(<Page />);

    const openSource = screen.getByRole('region', { name: 'Open source' });
    expect(within(openSource).getByRole('link', { name: GITHUB_URL })).toHaveAttribute(
      'href',
      GITHUB_URL,
    );
    expect(within(openSource).getByRole('link', { name: `${GITHUB_URL}/issues` })).toHaveAttribute(
      'href',
      `${GITHUB_URL}/issues`,
    );
  });
});
