import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setActiveWorkspaceId } from '@/lib/api/client';
import { ProjectProvider } from '@/lib/project/project-context';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import PromptsPage from './page';

const WORKSPACE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const SET_ID = '22222222-2222-4222-8222-222222222222';

function makePrompt(overrides: Record<string, unknown> = {}) {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    prompt_set_id: SET_ID,
    text: 'Best running shoes?',
    theme: 'Comfort',
    intent: 'discovery',
    branded: false,
    enabled: true,
    origin: 'manual',
    ...overrides,
  };
}

function makeSet(prompts: unknown[]) {
  return {
    id: SET_ID,
    project_id: PROJECT_ID,
    name: 'Default prompt set',
    description: '',
    prompt_count: prompts.length,
    prompts,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function makeProject(promptSets: unknown[]) {
  return {
    id: PROJECT_ID,
    workspace_id: WORKSPACE_ID,
    name: 'Searchify',
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
    prompt_sets: promptSets,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };
}

function renderPage() {
  return renderWithProviders(
    <ProjectProvider>
      <PromptsPage />
    </ProjectProvider>,
  );
}

/** Register the base handlers: project list + prompt-set list. */
function baseHandlers(prompts: unknown[]) {
  const set = makeSet(prompts);
  mswServer.use(
    http.get('/api/v1/projects', () => HttpResponse.json([makeProject([set])])),
    http.get('/api/v1/prompt-sets', () => HttpResponse.json([set])),
  );
  return set;
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  window.localStorage.clear();
  setActiveWorkspaceId(null);
});
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('PromptsPage', () => {
  it('renders the prompt table with a row per prompt', async () => {
    baseHandlers([makePrompt(), makePrompt({ id: '44444444-4444-4444-8444-444444444444', text: 'Nike vs Adidas', intent: 'comparison' })]);
    renderPage();

    expect(await screen.findByText('Best running shoes?', undefined, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText('Nike vs Adidas')).toBeInTheDocument();
  });

  it('shows the empty state when the set has no prompts', async () => {
    baseHandlers([]);
    renderPage();

    expect(await screen.findByText('No prompts yet', undefined, { timeout: 5000 })).toBeInTheDocument();
    // Both the toolbar and the empty-state card expose an "Add prompt" action.
    expect(screen.getAllByRole('button', { name: 'Add prompt' }).length).toBeGreaterThan(0);
  });

  it('renders the AI-suggest panel in its not-yet-enabled state', async () => {
    baseHandlers([makePrompt()]);
    renderPage();

    expect(
      await screen.findByText('Generate prompts & topics', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByText('Coming soon')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled();
  });

  it('filters by search', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt(), makePrompt({ id: '44444444-4444-4444-8444-444444444444', text: 'Nike vs Adidas', intent: 'comparison' })]);
    renderPage();

    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.type(screen.getByRole('searchbox', { name: 'Search prompts' }), 'nike');

    expect(screen.queryByText('Best running shoes?')).not.toBeInTheDocument();
    expect(screen.getByText('Nike vs Adidas')).toBeInTheDocument();
  });

  it('creates a prompt through the add dialog', async () => {
    const user = userEvent.setup();
    baseHandlers([]);
    let created: Record<string, unknown> | null = null;
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/prompts`, async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makePrompt({ text: created.text as string }), { status: 201 });
      }),
    );

    renderPage();
    await screen.findByText('No prompts yet', undefined, { timeout: 5000 });
    // The toolbar action is the first "Add prompt" button.
    await user.click(screen.getAllByRole('button', { name: 'Add prompt' })[0]);

    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText(/Prompt text/), 'Fresh prompt');
    await user.click(within(dialog).getByRole('button', { name: 'Add prompt' }));

    await waitFor(() => expect(created).not.toBeNull());
    expect(created).toMatchObject({ text: 'Fresh prompt', enabled: true });
  });

  it('toggles enabled via the row switch', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt({ enabled: true })]);
    let patched: Record<string, unknown> | null = null;
    mswServer.use(
      http.patch('/api/v1/prompts/:id', async ({ request }) => {
        patched = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makePrompt({ enabled: false }));
      }),
    );

    renderPage();
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('switch', { name: /disable prompt/i }));

    await waitFor(() => expect(patched).toEqual({ enabled: false }));
  });

  it('deletes a prompt via the row menu', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt()]);
    let deleted = false;
    mswServer.use(
      http.delete('/api/v1/prompts/:id', () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderPage();
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: 'Prompt actions' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Delete' }));

    await waitFor(() => expect(deleted).toBe(true));
  });

  it('parses, previews, and persists a CSV import', async () => {
    const user = userEvent.setup();
    baseHandlers([]);
    let imported: { prompts: unknown[] } | null = null;
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/import`, async ({ request }) => {
        imported = (await request.json()) as { prompts: unknown[] };
        return HttpResponse.json(makeSet([makePrompt({ origin: 'imported' })]), { status: 201 });
      }),
    );

    renderPage();
    await screen.findByText('No prompts yet', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: 'Import CSV' }));

    const dialog = await screen.findByRole('dialog');
    const file = new File(
      ['text,theme,intent\nBest shoes?,Comfort,discovery\n,MissingText,purchase\n'],
      'prompts.csv',
      { type: 'text/csv' },
    );
    await user.upload(within(dialog).getByLabelText('CSV file'), file);

    // Preview renders both rows; one is flagged invalid (empty text).
    expect(await within(dialog).findByText('Best shoes?')).toBeInTheDocument();
    expect(within(dialog).getByText(/1 skipped/)).toBeInTheDocument();

    // Only the valid row is importable.
    await user.click(within(dialog).getByRole('button', { name: /Import 1 prompt/ }));

    await waitFor(() => expect(imported).not.toBeNull());
    const payload = imported as { prompts: unknown[] } | null;
    if (!payload) throw new Error('import payload was not captured');
    const rows = payload.prompts;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ text: 'Best shoes?', intent: 'discovery' });
  });
});
