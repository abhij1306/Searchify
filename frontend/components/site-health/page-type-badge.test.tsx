import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { PageTypeBadge } from './page-type-badge';

describe('PageTypeBadge', () => {
  it('renders the humanized page-type label as a badge', () => {
    render(<PageTypeBadge pageType="about_contact" />);
    expect(screen.getByText('About / Contact')).toBeInTheDocument();
  });

  it('renders the acronym label untouched (FAQ, not Faq)', () => {
    render(<PageTypeBadge pageType="faq" />);
    expect(screen.getByText('FAQ')).toBeInTheDocument();
  });

  it('renders the — placeholder for an unclassified page (null) — never a guessed type', () => {
    render(<PageTypeBadge pageType={null} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders the — placeholder when the projection does not carry the field', () => {
    render(<PageTypeBadge pageType={undefined} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
