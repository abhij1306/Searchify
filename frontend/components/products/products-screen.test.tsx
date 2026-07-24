import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ProductsScreen } from './products-screen';

// The screen's tab state is URL-synced (?tab=); stub next/navigation with a
// controllable search-param + a replace spy (jsdom has no router).
const replaceSpy = vi.fn();
let urlTab: string | null = null;

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceSpy }),
  usePathname: () => '/products',
  useSearchParams: () => new URLSearchParams(urlTab ? `tab=${urlTab}` : ''),
}));

// Isolate the tab orchestration: the panels are stubbed (their own tests
// cover their contents); the project context resolves a fixed project.
vi.mock('@/lib/project/project-context', () => ({
  useProjectContext: () => ({
    activeProject: { id: '11111111-1111-4111-8111-111111111111' },
    isLoading: false,
  }),
}));

vi.mock('@/lib/products/use-products-screen', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/products/use-products-screen')>();
  return {
    ...original,
    useProductVisibilityQueries: () => ({
      auditsQuery: { isLoading: false },
      runOptions: [],
      activeRunId: null,
      selectRun: vi.fn(),
      engine: 'all',
      setEngine: vi.fn(),
      engineParam: undefined,
      visibilityQuery: { isLoading: true },
    }),
  };
});

vi.mock('./catalog-panel', () => ({
  CatalogPanel: () => <div data-testid="catalog-panel">Catalog panel</div>,
}));

vi.mock('./product-visibility-panel', () => ({
  ProductVisibilityPanel: () => <div data-testid="visibility-panel">Visibility panel</div>,
}));

describe('ProductsScreen tabs', () => {
  beforeEach(() => {
    replaceSpy.mockClear();
    urlTab = null;
  });

  it('defaults to the Catalog tab and renders exactly one panel', () => {
    render(<ProductsScreen />);

    expect(screen.getByRole('tab', { name: 'Catalog' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Visibility' })).toHaveAttribute(
      'aria-selected',
      'false',
    );
    expect(screen.getByTestId('catalog-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('visibility-panel')).not.toBeInTheDocument();
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
  });

  it('switches panels on tab click and mirrors the tab into ?tab=', async () => {
    const user = userEvent.setup();
    render(<ProductsScreen />);

    await user.click(screen.getByRole('tab', { name: 'Visibility' }));
    expect(screen.getByTestId('visibility-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('catalog-panel')).not.toBeInTheDocument();
    expect(replaceSpy).toHaveBeenCalledWith('/products?tab=visibility');

    await user.click(screen.getByRole('tab', { name: 'Catalog' }));
    expect(screen.getByTestId('catalog-panel')).toBeInTheDocument();
    expect(replaceSpy).toHaveBeenCalledWith('/products?tab=catalog');
  });

  it('reads the initial tab from ?tab= (invalid values fall back to Catalog)', () => {
    urlTab = 'visibility';
    render(<ProductsScreen />);
    expect(screen.getByTestId('visibility-panel')).toBeInTheDocument();
  });

  it('supports ArrowRight keyboard navigation between tabs', async () => {
    const user = userEvent.setup();
    render(<ProductsScreen />);

    screen.getByRole('tab', { name: 'Catalog' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Visibility' })).toHaveFocus();
    expect(screen.getByTestId('visibility-panel')).toBeInTheDocument();
  });
});
