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

type UserEvents = ReturnType<typeof userEvent.setup>;

/** Advance the wizard one step via the Next button. */
const next = (user: UserEvents) => user.click(screen.getByRole('button', { name: /^next$/i }));

/** Fill the Brand step's required fields. */
async function fillBrand(user: UserEvents) {
  await user.type(screen.getByLabelText(/brand name/i), 'Searchify');
  await user.type(screen.getByLabelText(/project name/i), 'Searchify — US');
  await user.type(screen.getByLabelText(/website url/i), 'https://searchify.com');
}

/** Walk Brand → Market → Domains → Competitors → Defaults with valid values. */
async function walkToDefaults(user: UserEvents) {
  await fillBrand(user);
  await next(user); // → Market (US/en prefilled)
  await next(user); // → Domains
  await next(user); // → Competitors
  await next(user); // → Defaults
}

describe('SetupForm — create (wizard)', () => {
  it('renders the stepper on the Brand step and blocks Next on empty required fields', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupForm />);

    // Horizontal stepper with all five steps; Brand is current.
    const stepper = screen.getByRole('list', { name: /setup steps/i });
    expect(stepper).toHaveTextContent('Brand');
    expect(stepper).toHaveTextContent('Market');
    expect(stepper).toHaveTextContent('Domains');
    expect(stepper).toHaveTextContent('Competitors');
    expect(stepper).toHaveTextContent('Defaults');

    // No Create button until the last step.
    expect(screen.queryByRole('button', { name: /create project/i })).not.toBeInTheDocument();

    await next(user);
    expect(await screen.findByText(/brand name is required/i)).toBeInTheDocument();
    expect(screen.getByText(/project name is required/i)).toBeInTheDocument();
    expect(screen.getByText(/website url is required/i)).toBeInTheDocument();
    // Still on the Brand step.
    expect(screen.getByLabelText(/brand name/i)).toBeInTheDocument();
  });

  it('validates a competitor domain row before leaving the Competitors step', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupForm />);

    await fillBrand(user);
    await next(user); // Market
    await next(user); // Domains
    await next(user); // Competitors

    await user.click(screen.getByRole('button', { name: /add competitor/i }));
    await user.type(screen.getByLabelText(/competitor name/i), 'Acme');
    await user.click(screen.getByRole('button', { name: /add domain/i }));
    await user.type(screen.getByLabelText(/^Domains 1$/i), 'not a domain');
    await next(user);

    expect(await screen.findByText(/enter a bare domain/i)).toBeInTheDocument();
    // Still on Competitors — the invalid row blocks advancing.
    expect(screen.getByLabelText(/competitor name/i)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it('walks all steps, creates the project, sets it active, and routes to /visibility', async () => {
    const user = userEvent.setup();
    let body: unknown;
    mswServer.use(
      http.post('/api/v1/projects', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(savedProject);
      }),
    );

    renderWithProviders(<SetupForm />);
    await fillBrand(user);
    // add a brand alias on the Brand step
    await user.click(screen.getByRole('button', { name: /^add alias$/i }));
    await user.type(screen.getByLabelText(/^Brand aliases 1$/i), 'Searchify AI');

    await next(user); // Market
    await next(user); // Domains
    await next(user); // Competitors
    await next(user); // Defaults

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

  it('keeps values when navigating back to an earlier step', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupForm />);

    await fillBrand(user);
    await next(user); // Market
    await user.click(screen.getByRole('button', { name: /^back$/i }));

    expect(screen.getByLabelText(/brand name/i)).toHaveValue('Searchify');
    expect(screen.getByLabelText(/project name/i)).toHaveValue('Searchify — US');
  });

  it('surfaces the ApiError message inline on a failed create', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.post('/api/v1/projects', () =>
        HttpResponse.json({ detail: 'Workspace limit reached.' }, { status: 400 }),
      ),
    );

    renderWithProviders(<SetupForm />);
    await walkToDefaults(user);
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

  it('prefills from the project, unlocks all steps, and PATCHes on save without redirecting', async () => {
    const user = userEvent.setup();
    let body: unknown;
    mswServer.use(
      http.patch(`/api/v1/projects/${existing.id}`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...existing, name: 'Searchify — EU' });
      }),
    );

    renderWithProviders(<SetupForm project={existing} />);

    // Brand step prefilled.
    expect(screen.getByLabelText(/brand name/i)).toHaveValue('Searchify');

    // Edit mode unlocks every step — jump straight to Competitors.
    await user.click(screen.getByRole('button', { name: /competitors/i }));
    expect(screen.getByLabelText(/competitor name/i)).toHaveValue('Acme');
    expect(screen.getByLabelText(/^Domains 1$/i)).toHaveValue('acme.com');

    // Back to Brand via the stepper, rename, and save from a non-final step.
    await user.click(screen.getByRole('button', { name: /brand/i }));
    const projectName = screen.getByLabelText(/project name/i);
    await user.clear(projectName);
    await user.type(projectName, 'Searchify — EU');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText(/project saved/i)).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
    expect(body).toMatchObject({ name: 'Searchify — EU', benchmark_mode: 'forced_grounded' });
  });
});

describe('SetupForm — AI suggestions', () => {
  it('disables the domains Generate button until a brand name is entered', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupForm />);

    // Walk to the Domains step without a brand name is impossible (Brand step
    // validates), so verify via edit mode with a cleared brand instead.
    await fillBrand(user);
    await next(user); // Market
    await next(user); // Domains

    expect(screen.getByRole('button', { name: /generate with ai/i })).toBeEnabled();

    // Going back and clearing the brand disables the button again.
    await user.click(screen.getByRole('button', { name: /^back$/i }));
    await user.click(screen.getByRole('button', { name: /^back$/i }));
    // The step remounted; re-query the (value-preserving) input.
    const brand = screen.getByLabelText(/brand name/i);
    expect(brand).toHaveValue('Searchify');
    await user.clear(brand);
    await next(user);
    expect(await screen.findByText(/brand name is required/i)).toBeInTheDocument();
  });

  it('appends suggested competitors after consent, skipping duplicates', async () => {
    const user = userEvent.setup();
    let body: Record<string, unknown> | null = null;
    mswServer.use(
      http.post('/api/v1/brand-suggestions/competitors', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          competitors: [
            { name: 'Acme', aliases: ['Acme Co'], domains: ['acme.com'] },
            { name: 'Globex', aliases: [], domains: ['globex.com'] },
          ],
          dropped_duplicates: 0,
        });
      }),
    );

    renderWithProviders(<SetupForm />);
    await fillBrand(user);
    await next(user); // Market
    await next(user); // Domains
    await next(user); // Competitors

    // A pre-existing competitor with a matching name must survive untouched
    // and suppress the duplicate suggestion.
    await user.click(screen.getByRole('button', { name: /add competitor/i }));
    await user.type(screen.getByLabelText(/competitor name/i), 'acme');

    await user.click(screen.getByRole('button', { name: /generate with ai/i }));

    // The consent checkbox gates the dialog's Generate action.
    const generate = screen.getByRole('button', { name: /^generate$/i });
    expect(generate).toBeDisabled();
    await user.click(
      screen.getByLabelText(/confirm sending brand details to the ai provider/i),
    );
    await user.click(generate);

    expect(
      await screen.findByText(/added 1 competitor to the form for review; 1 duplicate skipped/i),
    ).toBeInTheDocument();
    expect(body).toMatchObject({
      brand_name: 'Searchify',
      confirm_send_evidence: true,
      existing_competitor_names: ['acme'],
    });

    // Existing row is intact; only the non-duplicate suggestion was appended.
    const names = screen.getAllByLabelText(/competitor name/i);
    expect(names).toHaveLength(2);
    expect(names[0]).toHaveValue('acme');
    expect(names[1]).toHaveValue('Globex');
  });

  it('appends suggested owned domains without touching unintended domains', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.post('/api/v1/brand-suggestions/owned-domains', () =>
        HttpResponse.json({ domains: ['searchify.com', 'searchify.io'], dropped_duplicates: 0 }),
      ),
    );

    renderWithProviders(<SetupForm />);
    await fillBrand(user);
    await next(user); // Market
    await next(user); // Domains

    await user.click(screen.getByRole('button', { name: /generate with ai/i }));
    await user.click(
      screen.getByLabelText(/confirm sending brand details to the ai provider/i),
    );
    await user.click(screen.getByRole('button', { name: /^generate$/i }));

    expect(
      await screen.findByText(/added 2 owned domains to the form for review/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Owned domains 1$/i)).toHaveValue('searchify.com');
    expect(screen.getByLabelText(/^Owned domains 2$/i)).toHaveValue('searchify.io');
    expect(screen.queryByLabelText(/^Unintended domains 1$/i)).not.toBeInTheDocument();
  });

  it('shows the config guidance when no agent is configured (503)', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.post('/api/v1/brand-suggestions/owned-domains', () =>
        HttpResponse.json(
          { detail: { code: 'agent_not_configured', message: 'No default agent.' } },
          { status: 503 },
        ),
      ),
    );

    renderWithProviders(<SetupForm />);
    await fillBrand(user);
    await next(user); // Market
    await next(user); // Domains

    await user.click(screen.getByRole('button', { name: /generate with ai/i }));
    await user.click(
      screen.getByLabelText(/confirm sending brand details to the ai provider/i),
    );
    await user.click(screen.getByRole('button', { name: /^generate$/i }));

    expect(await screen.findByText(/no ai provider is configured/i)).toBeInTheDocument();
    expect(screen.getByText('DEFAULT_AGENT_API_KEY')).toBeInTheDocument();
  });
});
