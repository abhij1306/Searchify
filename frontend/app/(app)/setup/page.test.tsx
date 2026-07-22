import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

// Stub the router so the edit-form redirect is observable.
const replace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
}));

// Controllable project context — the page's redirect latch keys off whether a
// project existed when the query first resolved.
let contextValue: { activeProjectId: string | null; isLoading: boolean };
vi.mock('@/lib/project/project-context', () => ({
  useProjectContext: () => contextValue,
}));

// The wizard itself is covered by its own suite; the page only decides
// between redirecting and rendering it.
vi.mock('@/components/setup/setup-form', () => ({
  SetupForm: () => <div>setup-form-stub</div>,
}));

import SetupPage from './page';

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

afterEach(() => replace.mockReset());

describe('SetupPage', () => {
  it('redirects to the edit form when a project already exists on load', async () => {
    contextValue = { activeProjectId: PROJECT_ID, isLoading: false };
    render(<SetupPage />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith(`/setup/${PROJECT_ID}`));
  });

  it('does not hijack the create flow when the project appears after mount', () => {
    // Mounted with no project: renders the embedded create form.
    contextValue = { activeProjectId: null, isLoading: false };
    const { rerender } = render(<SetupPage />);
    expect(screen.getByText('setup-form-stub')).toBeInTheDocument();

    // The create flow sets the active project while this page is still
    // mounted and routes to /prompts itself — the latch must hold (effects
    // flush synchronously inside rerender's act).
    contextValue = { activeProjectId: PROJECT_ID, isLoading: false };
    rerender(<SetupPage />);
    expect(replace).not.toHaveBeenCalled();
  });
});
