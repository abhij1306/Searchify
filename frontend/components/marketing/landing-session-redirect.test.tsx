import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';

import { renderWithProviders } from '@/test/render';

import { LandingSessionRedirect } from './landing-session-redirect';

describe('LandingSessionRedirect', () => {
  it('renders children without force-redirecting signed-in or anonymous visitors off the landing page', () => {
    renderWithProviders(
      <LandingSessionRedirect>
        <div>marketing page content</div>
      </LandingSessionRedirect>,
    );

    expect(screen.getByText('marketing page content')).toBeInTheDocument();
  });
});
