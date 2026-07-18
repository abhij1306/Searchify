import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import { PagesTable } from './pages-table';
import type { PageSummary } from '@/lib/api/types';

// Stub next/navigation (unavailable in jsdom). `push` is asserted by the
// clickable-row test; vi.hoisted so the hoisted mock factory can reference it.
const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
}));

const UUID = '11111111-1111-4111-8111-111111111111';
const CRAWL = '22222222-2222-4222-8222-222222222222';

function page(overrides: Partial<PageSummary> = {}): PageSummary {
  return {
    site_url_id: UUID,
    crawl_id: CRAWL,
    normalized_url: 'https://acme.com/',
    display_url: 'https://acme.com/',
    title: 'Homepage',
    monitored: true,
    analysis_status: 'completed',
    error_code: '',
    issue_count: 3,
    technical_score: 46,
    aeo_score: 64,
    overall_score: 55,
    last_audited: '2026-07-16T00:00:00Z',
    ...overrides,
  };
}

describe('PagesTable', () => {
  it('renders scores for a completed page', () => {
    render(<PagesTable pages={[page()]} crawlId={CRAWL} />);
    expect(screen.getByText('Homepage')).toBeInTheDocument();
    expect(screen.getByText('46')).toBeInTheDocument();
    expect(screen.getByText('64')).toBeInTheDocument();
  });

  it('renders the "—" placeholder for a blocked page — never a fabricated zero', () => {
    render(
      <PagesTable
        crawlId={CRAWL}
        pages={[
          page({
            site_url_id: '33333333-3333-4333-8333-333333333333',
            title: 'Admin Panel',
            analysis_status: 'blocked',
            issue_count: null,
            technical_score: null,
            aeo_score: null,
            overall_score: null,
            last_audited: null,
          }),
        ]}
      />,
    );
    // No zeroes rendered for the missing scores.
    expect(screen.queryByText('0')).not.toBeInTheDocument();
    // Blocked status badge is shown.
    expect(screen.getByText('Blocked')).toBeInTheDocument();
    // Placeholder appears for the missing score/issue cells.
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('links View to the per-URL detail route', () => {
    render(<PagesTable pages={[page()]} crawlId={CRAWL} />);
    const view = screen.getByText('View');
    const anchor = view.closest('a');
    expect(anchor).not.toBeNull();
    expect(anchor).toHaveAttribute(
      'href',
      `/site-health/crawls/${CRAWL}/pages/${UUID}`,
    );
  });

  it('navigates to the per-URL detail when the row is clicked', () => {
    push.mockClear();
    render(<PagesTable pages={[page()]} crawlId={CRAWL} />);
    fireEvent.click(screen.getByText('Homepage'));
    expect(push).toHaveBeenCalledWith(
      `/site-health/crawls/${CRAWL}/pages/${UUID}`,
    );
  });
});
