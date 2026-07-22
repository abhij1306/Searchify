import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';
import type { BrandProfile, Project } from '@/lib/api/types';

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

const brandProfile: BrandProfile = {
  id: '11111111-1111-4111-8111-111111111111',
  workspace_id: savedProject.workspace_id,
  project_id: savedProject.id,
  brand_id: '22222222-2222-4222-8222-222222222222',
  description: 'AI visibility platform.',
  positioning: 'Specialist AEO analytics for marketing teams.',
  products_services: ['AEO analytics'],
  target_audience: 'Marketing teams',
  sources: {
    description: 'manual',
    positioning: 'manual',
    products_services: 'manual',
    target_audience: 'manual',
  },
  source_artifact_ids: {
    description: null,
    positioning: null,
    products_services: null,
    target_audience: null,
  },
  created_at: '2026-07-21T00:00:00Z',
  updated_at: '2026-07-21T00:00:00Z',
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

/** Fill the Brand step's required fields (create mode derives the project name). */
async function fillBrand(user: UserEvents) {
  await user.type(screen.getByLabelText(/brand name/i), 'Searchify');
  await user.type(screen.getByLabelText(/website url/i), 'https://searchify.com');
}

/** Walk the guided create flow (Brand → Market) with valid values. */
async function walkToMarket(user: UserEvents) {
  await fillBrand(user);
  await next(user); // → Market (US/en prefilled)
}

describe('SetupForm — create (guided)', () => {
  it('renders the two-step guided flow and blocks Next on empty required fields', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupForm />);

    // Guided stepper has only Brand + Market; Domains/Competitors/Defaults
    // are refined later on the edit surface.
    const stepper = screen.getByRole('list', { name: /setup steps/i });
    expect(stepper).toHaveTextContent('Brand');
    expect(stepper).toHaveTextContent('Market');
    expect(stepper).not.toHaveTextContent('Domains');
    expect(stepper).not.toHaveTextContent('Competitors');
    expect(stepper).not.toHaveTextContent('Defaults');
    expect(screen.getByText('Step 1 of 2')).toBeInTheDocument();

    // The project name is auto-derived, not asked.
    expect(screen.queryByLabelText(/project name/i)).not.toBeInTheDocument();

    await next(user);
    expect(await screen.findByText(/brand name is required/i)).toBeInTheDocument();
    expect(screen.getByText(/website url is required/i)).toBeInTheDocument();
    // Still on the Brand step.
    expect(screen.getByLabelText(/brand name/i)).toBeInTheDocument();
  });

  it('validates a competitor domain row in edit mode before leaving the Competitors step', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupForm project={savedProject} />);

    // Edit mode unlocks every step — jump straight to Competitors.
    await user.click(screen.getByRole('button', { name: /competitors/i }));
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

  it('walks both steps, creates the project with a derived name, and routes to /prompts', async () => {
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

    await next(user); // Market — searchable selects default to US / English
    expect(screen.getByLabelText(/^country$/i)).toHaveValue('United States');
    expect(screen.getByLabelText(/^language$/i)).toHaveValue('English');
    expect(screen.getByText('Step 2 of 2')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /create project/i }));

    await waitFor(() => expect(setActiveProjectId).toHaveBeenCalledWith(savedProject.id));
    expect(replace).toHaveBeenCalledWith('/prompts');
    expect(body).toMatchObject({
      brand_name: 'Searchify',
      name: 'Searchify', // derived from the brand name
      website_url: 'https://searchify.com',
      country_code: 'US',
      language_code: 'en',
      brand: { aliases: ['Searchify AI'] },
      benchmark_mode: 'consumer_like',
      default_repetitions: 3,
    });
  });

  it('keeps values when navigating back to an earlier step', async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupForm />);

    await fillBrand(user);
    await next(user); // Market
    await user.click(screen.getByRole('button', { name: /^back$/i }));

    expect(screen.getByLabelText(/brand name/i)).toHaveValue('Searchify');
    expect(screen.getByLabelText(/website url/i)).toHaveValue('https://searchify.com');
  });

  it('surfaces the ApiError message inline on a failed create', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.post('/api/v1/projects', () =>
        HttpResponse.json({ detail: 'Workspace limit reached.' }, { status: 400 }),
      ),
    );

    renderWithProviders(<SetupForm />);
    await walkToMarket(user);
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

    // A persisted project opens on the completed Defaults step after a refresh.
    expect(screen.getByLabelText(/benchmark mode/i)).toBeInTheDocument();

    // Brand remains reachable and is prefilled.
    await user.click(screen.getByRole('button', { name: /brand/i }));
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
    // Edit mode: every step is unlocked, and the button tracks the live
    // brand-name value (the backend also 422s on an empty brand_name).
    renderWithProviders(<SetupForm project={savedProject} />);

    await user.click(screen.getByRole('button', { name: /domains/i }));
    expect(screen.getByRole('button', { name: /generate with ai/i })).toBeEnabled();

    // Clearing the brand on the Brand step disables the button again.
    await user.click(screen.getByRole('button', { name: /brand/i }));
    await user.clear(screen.getByLabelText(/brand name/i));
    await user.click(screen.getByRole('button', { name: /domains/i }));
    expect(screen.getByRole('button', { name: /generate with ai/i })).toBeDisabled();
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

    renderWithProviders(<SetupForm project={savedProject} />);
    await user.click(screen.getByRole('button', { name: /competitors/i }));

    // A pre-existing competitor with a matching name must survive untouched
    // and suppress the duplicate suggestion.
    await user.click(screen.getByRole('button', { name: /add competitor/i }));
    await user.type(screen.getByLabelText(/competitor name/i), 'acme');

    await user.click(screen.getByRole('button', { name: /generate with ai/i }));

    // The consent checkbox gates the dialog's Generate action.
    const generate = screen.getByRole('button', { name: /^generate$/i });
    expect(generate).toBeDisabled();
    await user.click(screen.getByLabelText(/confirm sending brand details to the ai provider/i));
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

  it('includes curated profile context for competitor suggestions in edit mode', async () => {
    const user = userEvent.setup();
    let body: Record<string, unknown> | null = null;
    mswServer.use(
      http.post('/api/v1/brand-suggestions/competitors', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ competitors: [], dropped_duplicates: 0 });
      }),
    );

    renderWithProviders(<SetupForm project={savedProject} brandProfile={brandProfile} />);
    await user.click(screen.getByRole('button', { name: /competitors/i }));
    await user.click(screen.getByRole('button', { name: /generate with ai/i }));
    await user.click(screen.getByLabelText(/confirm sending brand details/i));
    await user.click(screen.getByRole('button', { name: /^generate$/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({
      positioning: brandProfile.positioning,
      products_services: brandProfile.products_services,
      target_audience: brandProfile.target_audience,
    });
  });

  it('appends suggested owned domains without touching unintended domains', async () => {
    const user = userEvent.setup();
    mswServer.use(
      http.post('/api/v1/brand-suggestions/owned-domains', () =>
        HttpResponse.json({ domains: ['searchify.com', 'searchify.io'], dropped_duplicates: 0 }),
      ),
    );

    renderWithProviders(<SetupForm project={savedProject} />);
    await user.click(screen.getByRole('button', { name: /domains/i }));

    await user.click(screen.getByRole('button', { name: /generate with ai/i }));
    await user.click(screen.getByLabelText(/confirm sending brand details to the ai provider/i));
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

    renderWithProviders(<SetupForm project={savedProject} />);
    await user.click(screen.getByRole('button', { name: /domains/i }));

    await user.click(screen.getByRole('button', { name: /generate with ai/i }));
    await user.click(screen.getByLabelText(/confirm sending brand details to the ai provider/i));
    await user.click(screen.getByRole('button', { name: /^generate$/i }));

    expect(await screen.findByText(/no ai provider is configured/i)).toBeInTheDocument();
    expect(screen.getByText('DEFAULT_AGENT_API_KEY')).toBeInTheDocument();
  });
});
