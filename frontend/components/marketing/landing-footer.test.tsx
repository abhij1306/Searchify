import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { COMPETITORS } from '@/lib/marketing-content/compare';

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

  it('links the key routes and keeps every column link on-site', () => {
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

    // Resources lost Documentation; Company lost GitHub — the repo is private.
    expect(screen.queryByRole('link', { name: 'Documentation' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'GitHub' })).toBeNull();

    expect(screen.queryByRole('link', { name: 'Contact' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/login');
    expect(screen.getByRole('link', { name: 'Get started' })).toHaveAttribute('href', '/register');
  });

  it('renders no social row while SOCIAL_LINKS is empty', () => {
    const { container } = render(<LandingFooter />);

    expect(container.querySelector('.social-row')).toBeNull();
    expect(screen.queryByRole('link', { name: 'GitHub' })).toBeNull();
  });

  it('renders the legal row without a license link', () => {
    render(<LandingFooter />);

    expect(screen.getByText(/© 2026 Searchify · A CUBE27 product/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /MIT License/i })).toBeNull();

    expect(screen.queryByRole('link', { name: 'Privacy' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Terms' })).toBeNull();
  });
});
