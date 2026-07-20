import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import type { Project } from '@/lib/api/types';

// Stub next/navigation (unavailable in jsdom) so we can assert the redirect.
const replace = vi.fn();
const back = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace, back, push: vi.fn(), refresh: vi.fn() }),
}));

// Stub the F5 project context so the form can set the active project without a
// full ProjectProvider (which would fire its own /projects query).
const setActiveProjectId = vi.fn();
vi.mock('@/lib/project/project-context', () => ({
  useProjectContext: () => ({ setActiveProjectId }),
}));

import { SetupForm } from './setup-form';

const savedProject: Project = {
  id: '55555555-5555-4555-8555-555555555555',
  workspace_id: '66666666-6666-4666-8666-666666666666',
  name: 'Searchify — US',
  brand_name: 'Searchify',
  website_url: 'https://searchify.com',
  country_code: 'US',
  language_code: 'en',
  benchmark_mode: 'consumer_like',
  default_repetitions: 3,
  brand: { aliases: [] },
  owned_domains: [],
  unintended_domains: [],
  competitors: [],
  prompt_sets: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  mswServer.resetHandlers();
  replace.mockReset();
  back.mockReset();
  setActiveProjectId.mockReset();
});
afterAll(() => mswServer.close());

describe('SetupForm — create', () => {
  it('shows validation errors and does not POST on an empty submit', async () => {
    const user = userEvent.setup();
    const createHandler = vi.fn();
    mswServer.use(
      http.post('/api/v1/projects', () => {
        createHandler();
        return HttpResponse.json(savedProject);
      }),
    );

    renderWithProviders(<SetupForm />);
    await user.click(screen.getByRole('button', { name: /create project/i }));

    expect(await screen.findByText(/brand name is required/i)).toBeInTheDocument();
    expect(screen.getByText(/project name is required/i)).toBeInTheDocument();
    expect(screen.getByText(/website url is required/i)).toBeInTheDocument();
    expect(createHandler).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it('validates a competitor domain row before submitting', async () => {
    const user = userEvent.setup();
    mswServer.use(http.post('/api/v1/projects', () => HttpResponse.json(savedProject)));

    renderWithProviders(<SetupForm />);
    await user.type(screen.getByLabelText(/brand name/i), 'Searchify');
    await user.type(screen.getByLabelText(/project name/i), 'Searchify — US');
    await user.type(screen.getByLabelText(/website url/i), 'https://searchify.com');

    await user.click(screen.getByRole('button', { name: /add competitor/i }));
    await user.type(screen.getByLabelText(/competitor name/i), 'Acme');
    await user.click(screen.getByRole('button', { name: /add domain/i }));
    await user.type(screen.getByLabelText(/^Domains 1$/i), 'not a domain');
    await user.click(screen.getByRole('button', { name: /create project/i }));

    expect(await screen.findByText(/enter a bare domain/i)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it('creates a project, sets it active, and routes to /visibility', async () => {
    const user = userEvent.setup();
    let body: unknown;
    mswServer.use(
      http.post('/api/v1/projects', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(savedProject);
      }),
    );

    renderWithProviders(<SetupForm />);
    await user.type(screen.getByLabelText(/brand name/i), 'Searchify');
    await user.type(screen.getByLabelText(/project name/i), 'Searchify — US');
    await user.type(screen.getByLabelText(/website url/i), 'https://searchify.com');

    // add a brand alias
    await user.click(screen.getByRole('button', { name: /^add alias$/i }));
    await user.type(screen.getByLabelText(/^Brand aliases 1$/i), 'Searchify AI');

    await user.click(screen.getByRole('button', { name: /create project/i }));

    await waitFor(() => expect(setActiveProjectId).toHaveBeenCalledWith(savedProject.id));
    expect(replace).toHaveBeenCalledWith('/visibility');
    expect(body).toMatchObject({
      brand_name: 'Searchify',
      name: 'Searchify — US',
      website_url: 'https://searchify.com',
      country_code: 'US',
      brand: { aliases: ['Searchify AI'] },
      benchmark_mode: 'consumer_like',
    });
  });

  it('surfaces the ApiError message inline on a failed create', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.post('/api/v1/projects', () =>
        HttpResponse.json({ detail: 'Workspace limit reached.' }, { status: 400 }),
      ),
    );

    renderWithProviders(<SetupForm />);
    await user.type(screen.getByLabelText(/brand name/i), 'Searchify');
    await user.type(screen.getByLabelText(/project name/i), 'Searchify — US');
    await user.type(screen.getByLabelText(/website url/i), 'https://searchify.com');
    await user.click(screen.getByRole('button', { name: /create project/i }));

    expect(await screen.findByText(/workspace limit reached/i)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});

describe('SetupForm — edit', () => {
  const existing: Project = {
    ...savedProject,
    benchmark_mode: 'forced_grounded',
    default_repetitions: 4,
    brand: { aliases: ['Searchify AI'] },
    owned_domains: ['searchify.com'],
    competitors: [
      {
        id: '77777777-7777-4777-8777-777777777777',
        name: 'Acme',
        aliases: [],
        domains: ['acme.com'],
      },
    ],
  };

  it('prefills from the project and PATCHes on save without redirecting', async () => {
    const user = userEvent.setup();
    let body: unknown;
    mswServer.use(
      http.patch(`/api/v1/projects/${existing.id}`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...existing, name: 'Searchify — EU' });
      }),
    );

    renderWithProviders(<SetupForm project={existing} />);

    // Prefilled values are present.
    expect(screen.getByLabelText(/brand name/i)).toHaveValue('Searchify');
    expect(screen.getByLabelText(/competitor name/i)).toHaveValue('Acme');
    expect(screen.getByLabelText(/^Domains 1$/i)).toHaveValue('acme.com');

    const projectName = screen.getByLabelText(/project name/i);
    await user.clear(projectName);
    await user.type(projectName, 'Searchify — EU');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText(/project saved/i)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
    expect(body).toMatchObject({ name: 'Searchify — EU', benchmark_mode: 'forced_grounded' });
  });
});
