import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setActiveWorkspaceId } from '@/lib/api/client';
import { ProjectProvider } from '@/lib/project/project-context';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import PromptResearchPage from './page';

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
    status: 'active',
    origin: 'manual',
    ...overrides,
  };
}

function makeTopic(overrides: Record<string, unknown> = {}) {
  return {
    id: '55555555-5555-4555-8555-555555555555',
    project_id: PROJECT_ID,
    name: 'Footwear',
    description: '',
    origin: 'manual',
    active_count: 0,
    proposed_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
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
      <PromptResearchPage />
    </ProjectProvider>,
  );
}

/** Register the base handlers: project list + prompt-set list + topics. */
function baseHandlers(prompts: unknown[], topics: unknown[] = []) {
  const set = makeSet(prompts);
  mswServer.use(
    http.get('/api/v1/projects', () => HttpResponse.json([makeProject([set])])),
    http.get('/api/v1/prompt-sets', () => HttpResponse.json([set])),
    http.get(`/api/v1/projects/${PROJECT_ID}/topics`, () => HttpResponse.json(topics)),
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

describe('PromptResearchPage', () => {
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

  it('generates prompts through the consent-gated dialog', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt()]);
    let generateBody: Record<string, unknown> | null = null;
    const proposed = makePrompt({
      id: '66666666-6666-4666-8666-666666666666',
      text: 'Best trail runners?',
      theme: 'Footwear',
      status: 'proposed',
      origin: 'generated',
      topic_id: '55555555-5555-4555-8555-555555555555',
    });
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/generate`, async ({ request }) => {
        generateBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            generated: [proposed],
            topics: [makeTopic({ origin: 'generated', proposed_count: 1 })],
            dropped_duplicates: 0,
          },
          { status: 201 },
        );
      }),
    );

    renderPage();
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: /Generate prompts & topics/ }));

    const dialog = await screen.findByRole('dialog');
    // The Generate action stays disabled until the user consents to sending
    // brand evidence to the AI provider.
    const generateButton = within(dialog).getByRole('button', { name: 'Generate' });
    expect(generateButton).toBeDisabled();
    await user.click(
      within(dialog).getByRole('checkbox', {
        name: /Confirm sending brand details/i,
      }),
    );
    expect(generateButton).toBeEnabled();
    await user.click(generateButton);

    await waitFor(() => expect(generateBody).not.toBeNull());
    expect(generateBody).toMatchObject({ confirm_send_evidence: true, count: 10 });
    // Success summary reports the placement (proposed) + auto-switch to the
    // Proposed tab with the new prompt.
    expect(await within(dialog).findByText(/1 prompt proposed for review/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(screen.getByRole('tab', { name: /Proposed/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('selects the Active tab and reports placement when generated prompts land active', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt()]);
    const generatedActive = makePrompt({
      id: '66666666-6666-4666-8666-666666666666',
      text: 'Auto-promoted prompt',
      status: 'active',
      origin: 'generated',
    });
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/generate`, () =>
        HttpResponse.json(
          { generated: [generatedActive], topics: [], dropped_duplicates: 0 },
          { status: 201 },
        ),
      ),
    );

    renderPage();
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: /Generate prompts & topics/ }));

    const dialog = await screen.findByRole('dialog');
    await user.click(
      within(dialog).getByRole('checkbox', { name: /Confirm sending brand details/i }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Generate' }));

    // Summary reports the Active placement, not an unconditional "proposed".
    expect(await within(dialog).findByText(/1 prompt added to Active/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Close' }));
    // The Active tab (which holds the generated row) stays selected — the user
    // is not dumped on an empty Proposed tab.
    expect(screen.getByRole('tab', { name: /Active/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /Proposed/ })).toHaveAttribute(
      'aria-selected',
      'false',
    );
  });

  it('surfaces a topic load failure in the rail', async () => {
    const set = makeSet([makePrompt()]);
    mswServer.use(
      http.get('/api/v1/projects', () => HttpResponse.json([makeProject([set])])),
      http.get('/api/v1/prompt-sets', () => HttpResponse.json([set])),
      http.get(`/api/v1/projects/${PROJECT_ID}/topics`, () =>
        HttpResponse.json({ detail: 'boom' }, { status: 400 }),
      ),
    );

    renderPage();
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    expect(await screen.findAllByText(/Couldn't load topics/)).not.toHaveLength(0);
  });

  it('shows actionable config guidance when no agent is configured (503)', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt()]);
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/generate`, () =>
        HttpResponse.json(
          { detail: { code: 'agent_not_configured', message: 'No default agent' } },
          { status: 503 },
        ),
      ),
    );

    renderPage();
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: /Generate prompts & topics/ }));

    const dialog = await screen.findByRole('dialog');
    await user.click(
      within(dialog).getByRole('checkbox', { name: /Confirm sending brand details/i }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Generate' }));

    expect(await within(dialog).findByText(/No AI provider is configured/)).toBeInTheDocument();
    expect(within(dialog).getByText('DEFAULT_AGENT_API_KEY')).toBeInTheDocument();
  });

  it('splits prompts across status tabs and accepts all proposed', async () => {
    const user = userEvent.setup();
    baseHandlers([
      makePrompt(),
      makePrompt({
        id: '77777777-7777-4777-8777-777777777777',
        text: 'Proposed prompt one',
        status: 'proposed',
        origin: 'generated',
      }),
      makePrompt({
        id: '99999999-9999-4999-8999-999999999999',
        text: 'Unrelated draft',
        status: 'proposed',
        origin: 'generated',
      }),
    ]);
    let bulkBody: Record<string, unknown> | null = null;
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/prompts/bulk-status`, async ({ request }) => {
        bulkBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeSet([makePrompt()]));
      }),
    );

    renderPage();
    // Active tab shows only the active prompt.
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    expect(screen.queryByText('Proposed prompt one')).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /Proposed/ }));
    expect(screen.getByText('Proposed prompt one')).toBeInTheDocument();
    expect(screen.getByText('Unrelated draft')).toBeInTheDocument();
    expect(screen.queryByText('Best running shoes?')).not.toBeInTheDocument();

    // Narrow with search first: Accept all must only send the visible
    // (matching) prompt, not every proposed prompt.
    await user.type(screen.getByRole('searchbox', { name: 'Search prompts' }), 'Proposed prompt');
    expect(screen.queryByText('Unrelated draft')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Accept all' }));
    await waitFor(() => expect(bulkBody).not.toBeNull());
    expect(bulkBody).toMatchObject({
      prompt_ids: ['77777777-7777-4777-8777-777777777777'],
      status: 'active',
    });
  });

  it('filters by topic from the topics rail and creates topics', async () => {
    const user = userEvent.setup();
    const topic = makeTopic({ active_count: 1 });
    baseHandlers(
      [
        makePrompt({ topic_id: topic.id, text: 'Topic-scoped prompt' }),
        makePrompt({ id: '88888888-8888-4888-8888-888888888888', text: 'Unfiled prompt' }),
      ],
      [topic],
    );
    let createdTopic: Record<string, unknown> | null = null;
    mswServer.use(
      http.post(`/api/v1/projects/${PROJECT_ID}/topics`, async ({ request }) => {
        createdTopic = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(makeTopic({ name: createdTopic.name as string }), {
          status: 201,
        });
      }),
    );

    renderPage();
    await screen.findByText('Topic-scoped prompt', undefined, { timeout: 5000 });
    expect(screen.getByText('Unfiled prompt')).toBeInTheDocument();

    // Selecting the topic narrows the table to its prompts (the accessible
    // name includes the count suffix, so match on prefix).
    await user.click(await screen.findByRole('button', { name: /^Footwear/ }));
    expect(screen.queryByText('Unfiled prompt')).not.toBeInTheDocument();
    expect(screen.getByText('Topic-scoped prompt')).toBeInTheDocument();

    // Inline add-topic form posts the new name.
    await user.click(screen.getByRole('button', { name: 'Add topic' }));
    await user.type(screen.getByRole('textbox', { name: 'Topic name' }), 'Apparel');
    await user.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(createdTopic).toEqual({ name: 'Apparel' }));
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
