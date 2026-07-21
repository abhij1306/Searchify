import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { COMPETITORS } from '@/lib/marketing-content/compare';
import { GITHUB_URL, LICENSE_URL } from '@/lib/marketing-content/social';

import { LandingFooter } from './landing-footer';

// The footer is a pure presentational component — plain render, no providers
// or MSW (same convention as the other marketing chrome tests).
describe('LandingFooter (marketing chrome)', () => {
  it('keeps the .footer root and renders five labelled link columns in the Footer nav', () => {
    const { container } = render(<LandingFooter />);

    // The shared marketing.css transition list targets .footer — pin it.
    expect(container.querySelector('footer.footer')).not.toBeNull();

    const footerNav = screen.getByRole('navigation', { name: 'Footer' });
    expect(footerNav.querySelectorAll('.footer-col')).toHaveLength(5);
    const labels = Array.from(footerNav.querySelectorAll('.f-col-label')).map(
      (el) => el.textContent,
    );
    expect(labels).toEqual(['Product', 'Resources', 'Solutions', 'Compare', 'Company']);
  });

  it('links the key routes, with Documentation as the lone external column link', () => {
    render(<LandingFooter />);

    expect(screen.getByRole('link', { name: 'Features' })).toHaveAttribute('href', '/#features');
    expect(screen.getByRole('link', { name: 'How it works' })).toHaveAttribute(
      'href',
      '/#how-it-works',
    );
    expect(screen.getByRole('link', { name: 'Evidence' })).toHaveAttribute('href', '/#evidence');
    expect(screen.getByRole('link', { name: 'All comparisons' })).toHaveAttribute(
      'href',
      '/compare',
    );

    // The Compare column derives from the COMPETITORS content module.
    for (const competitor of COMPETITORS) {
      expect(screen.getByRole('link', { name: `vs ${competitor.name}` })).toHaveAttribute(
        'href',
        `/compare/${competitor.slug}`,
      );
    }

    const docs = screen.getByRole('link', { name: 'Documentation' });
    expect(docs).toHaveAttribute('href', GITHUB_URL);
    expect(docs).toHaveAttribute('target', '_blank');
    expect(docs.getAttribute('rel')).toContain('noreferrer');

    expect(screen.queryByRole('link', { name: 'Contact' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
    expect(screen.getByRole('link', { name: 'Get started' })).toHaveAttribute('href', '/register');
  });

  it('renders only published social destinations', () => {
    const { container } = render(<LandingFooter />);

    const socialRow = container.querySelector('.social-row');
    if (!(socialRow instanceof HTMLElement)) {
      throw new Error('footer social row missing');
    }
    const socials = within(socialRow);

    const github = socials.getByRole('link', { name: 'GitHub' });
    expect(github).toHaveAttribute('href', GITHUB_URL);
    expect(github).toHaveAttribute('target', '_blank');
    expect(github.getAttribute('rel')).toContain('noreferrer');

    expect(socials.getAllByRole('link')).toHaveLength(1);
  });

  it('renders the legal row with the published MIT License link', () => {
    render(<LandingFooter />);

    expect(screen.getByText(/© 2026 Searchify · A CUBE27 product/)).toBeInTheDocument();

    const mit = screen.getByRole('link', { name: 'MIT License' });
    expect(mit).toHaveAttribute('href', LICENSE_URL);
    expect(mit).toHaveAttribute('target', '_blank');
    expect(mit.getAttribute('rel')).toContain('noreferrer');

    expect(screen.queryByRole('link', { name: 'Privacy' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Terms' })).toBeNull();
  });
});
