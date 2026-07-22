import { http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setActiveWorkspaceId } from '@/lib/api/client';
import { ProjectProvider } from '@/lib/project/project-context';
import { mswServer } from '@/test/msw-server';
import { renderWithProviders } from '@/test/render';

import PromptsPage from './page';

// ---------------------------------------------------------------------------
// next/navigation mock — a controllable URL so the `?mode=manage` deep link
// can be exercised. Reset per test; the manage-mode block sets it before
// rendering.
// ---------------------------------------------------------------------------
let currentSearch = new URLSearchParams();
const replaceMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/prompts',
  useSearchParams: () => currentSearch,
}));

const WORKSPACE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const SET_ID = '22222222-2222-4222-8222-222222222222';
const TOPIC_ID = '55555555-5555-4555-8555-555555555555';

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
    id: TOPIC_ID,
    project_id: PROJECT_ID,
    name: 'Footwear',
    description: '',
    origin: 'manual',
    active_count: 1,
    proposed_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeEvidenceItem(promptId: string, brandMentioned: boolean, taskId: string) {
  return {
    audit_id: '99999999-9999-4999-8999-999999999999',
    task_id: taskId,
    analysis_id: taskId,
    artifact_id: null,
    prompt_snapshot_id: taskId,
    prompt_id: promptId,
    prompt_index: 0,
    prompt_text: 'Best running shoes?',
    repetition: 1,
    completed_at: '2026-01-02T00:00:00Z',
    logical_engine: 'chatgpt',
    transport_provider: 'openai',
    transport_model: 'gpt-test',
    search_used: false,
    search_query_count: 0,
    query_text_available: false,
    state: 'count_only',
    search_events: [],
    event_source: 'none',
    mentions: brandMentioned
      ? [
          {
            kind: 'brand',
            name: 'Searchify',
            first_offset: 0,
            artifact_id: null,
            analyzer_version: 'v1',
          },
        ]
      : [],
    citations: [],
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

function baseHandlers(prompts: unknown[], topics: unknown[] = [], evidenceItems: unknown[] = []) {
  const set = makeSet(prompts);
  mswServer.use(
    http.get('/api/v1/projects', () => HttpResponse.json([makeProject([set])])),
    http.get('/api/v1/prompt-sets', () => HttpResponse.json([set])),
    http.get(`/api/v1/projects/${PROJECT_ID}/topics`, () => HttpResponse.json(topics)),
    http.get(`/api/v1/projects/${PROJECT_ID}/visibility/evidence`, () =>
      HttpResponse.json({ items: evidenceItems, truncated: false }),
    ),
  );
  return set;
}

function renderPage() {
  return renderWithProviders(
    <ProjectProvider>
      <PromptsPage />
    </ProjectProvider>,
  );
}

beforeAll(() => mswServer.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  window.localStorage.clear();
  setActiveWorkspaceId(null);
  currentSearch = new URLSearchParams();
  replaceMock.mockReset();
});
afterEach(() => mswServer.resetHandlers());
afterAll(() => mswServer.close());

describe('PromptsPage (Your Prompts)', () => {
  it('groups active prompts by topic with a summary banner and a manage link', async () => {
    baseHandlers(
      [
        makePrompt({ topic_id: TOPIC_ID }),
        makePrompt({
          id: '44444444-4444-4444-8444-444444444444',
          text: 'Ungrouped prompt',
        }),
        // Proposed prompts never appear on Your Prompts.
        makePrompt({
          id: '66666666-6666-4666-8666-666666666666',
          text: 'Proposed prompt',
          status: 'proposed',
        }),
      ],
      [makeTopic()],
    );
    renderPage();

    expect(
      await screen.findByText('Best running shoes?', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    // Banner counts only active prompts (2) and topics with prompts (1).
    expect(screen.getByText('2')).toBeInTheDocument();
    // The banner's manage link enters the in-page manage mode deep link.
    expect(screen.getByRole('link', { name: 'Manage prompts' })).toHaveAttribute(
      'href',
      '/prompts?mode=manage',
    );
    // Topic group header + ungrouped bucket.
    expect(screen.getAllByText('Footwear').length).toBeGreaterThan(0);
    expect(screen.getByText('Ungrouped')).toBeInTheDocument();
    expect(screen.queryByText('Proposed prompt')).not.toBeInTheDocument();
  });

  it('collapses a topic group when its expander is toggled', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt({ topic_id: TOPIC_ID })], [makeTopic()]);
    renderPage();

    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: 'Collapse topic Footwear' }));
    expect(screen.queryByText('Best running shoes?')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Expand topic Footwear' }));
    expect(screen.getByText('Best running shoes?')).toBeInTheDocument();
  });

  it('derives per-prompt visibility scores from persisted evidence', async () => {
    const promptId = '33333333-3333-4333-8333-333333333333';
    baseHandlers(
      [makePrompt({ topic_id: TOPIC_ID })],
      [makeTopic()],
      [
        makeEvidenceItem(promptId, true, 'bbbbbbbb-0000-4000-8000-000000000001'),
        makeEvidenceItem(promptId, true, 'bbbbbbbb-0000-4000-8000-000000000002'),
        makeEvidenceItem(promptId, false, 'bbbbbbbb-0000-4000-8000-000000000003'),
      ],
    );
    renderPage();

    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    // 2 of 3 executions mentioned the brand → 67%, on both the prompt row and
    // the single-prompt topic group row.
    expect(await screen.findAllByText('67%')).toHaveLength(2);
  });

  it('shows the empty state pointing to manage mode when no active prompts exist', async () => {
    baseHandlers([]);
    renderPage();

    expect(
      await screen.findByText('No active prompts yet', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /Manage prompts/ }).length).toBeGreaterThan(0);
  });

  it('filters prompts by search', async () => {
    const user = userEvent.setup();
    baseHandlers([
      makePrompt(),
      makePrompt({ id: '44444444-4444-4444-8444-444444444444', text: 'Nike vs Adidas' }),
    ]);
    renderPage();

    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.type(screen.getByRole('searchbox', { name: 'Search prompts' }), 'nike');

    expect(screen.queryByText('Best running shoes?')).not.toBeInTheDocument();
    expect(screen.getByText('Nike vs Adidas')).toBeInTheDocument();
  });

  it('toggles between the read view and manage mode via the in-page controls', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt({ topic_id: TOPIC_ID })], [makeTopic()]);
    renderPage();

    // Read view first: the summary banner and the manage control are visible.
    await screen.findByText(/configuration includes/, undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: 'Manage prompts' }));

    // Manage mode swaps in the prompt library workspace.
    expect(
      await screen.findByRole(
        'button',
        { name: /Generate prompts & topics/ },
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();

    // Exiting returns to the read view without a navigation.
    await user.click(screen.getByRole('button', { name: 'Done managing' }));
    expect(
      await screen.findByText(/configuration includes/, undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Manage prompts' })).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it('clears the ?mode=manage param when leaving manage mode so manage links stay live', async () => {
    const user = userEvent.setup();
    currentSearch = new URLSearchParams('mode=manage');
    baseHandlers([makePrompt({ topic_id: TOPIC_ID })], [makeTopic()]);
    renderPage();

    // Deep-linked into manage mode.
    expect(
      await screen.findByRole('button', { name: /Generate prompts & topics/ }, { timeout: 5000 }),
    ).toBeInTheDocument();

    // Exiting clears the URL param (the read view's manage links point at
    // /prompts?mode=manage and would no-op against the current URL).
    await user.click(screen.getByRole('button', { name: 'Done managing' }));
    expect(replaceMock).toHaveBeenCalledWith('/prompts');
  });
});

// Manage mode — the full PromptLibrary workspace rendered in-page, entered
// here via the `?mode=manage` deep link (set before each render).
describe('PromptsPage manage mode (PromptLibrary)', () => {
  beforeEach(() => {
    currentSearch = new URLSearchParams('mode=manage');
  });

  it('renders the prompt table with a row per prompt', async () => {
    baseHandlers([
      makePrompt(),
      makePrompt({
        id: '44444444-4444-4444-8444-444444444444',
        text: 'Nike vs Adidas',
        intent: 'comparison',
      }),
    ]);
    renderPage();

    expect(
      await screen.findByText('Best running shoes?', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByText('Nike vs Adidas')).toBeInTheDocument();
  });

  it('shows the empty state when the set has no prompts', async () => {
    baseHandlers([]);
    renderPage();

    expect(
      await screen.findByText('No prompts yet', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
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
    expect(screen.getByRole('tab', { name: /Proposed/ })).toHaveAttribute('aria-selected', 'true');
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
    expect(screen.getByRole('tab', { name: /Proposed/ })).toHaveAttribute('aria-selected', 'false');
  });

  it('shows a fresh error only, never a stale success summary, on a failed retry', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt()]);
    const proposed = makePrompt({
      id: '66666666-6666-4666-8666-666666666666',
      text: 'Best trail runners?',
      status: 'proposed',
      origin: 'generated',
      topic_id: '55555555-5555-4555-8555-555555555555',
    });
    // First call succeeds, the retry fails with a provider error (502).
    let calls = 0;
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/generate`, () => {
        calls += 1;
        if (calls === 1) {
          return HttpResponse.json(
            {
              generated: [proposed],
              topics: [makeTopic({ origin: 'generated', proposed_count: 1 })],
              dropped_duplicates: 0,
            },
            { status: 201 },
          );
        }
        return HttpResponse.json(
          { detail: { code: 'provider_error', message: 'boom' } },
          { status: 502 },
        );
      }),
    );

    renderPage();
    await screen.findByText('Best running shoes?', undefined, { timeout: 5000 });
    await user.click(screen.getByRole('button', { name: /Generate prompts & topics/ }));

    const dialog = await screen.findByRole('dialog');
    await user.click(
      within(dialog).getByRole('checkbox', { name: /Confirm sending brand details/i }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Generate' }));
    expect(await within(dialog).findByText(/1 prompt proposed for review/)).toBeInTheDocument();

    // Retry fails: the stale success summary must be gone, only the error shows.
    await user.click(within(dialog).getByRole('button', { name: 'Generate' }));
    expect(await within(dialog).findByText(/The AI provider call failed/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/1 prompt proposed for review/)).not.toBeInTheDocument();
    expect(within(dialog).queryByText(/Generated 1 prompt/)).not.toBeInTheDocument();
  });

  it('counts only topics that received generated rows, not duplicate-only touched topics', async () => {
    const user = userEvent.setup();
    baseHandlers([makePrompt()]);
    // One generated row lands in a single topic, but the run "touched" two
    // topics (the second only had a dropped duplicate). The summary must say
    // 1 topic, not 2, and still report the dropped duplicate.
    const generated = makePrompt({
      id: '66666666-6666-4666-8666-666666666666',
      text: 'Best trail runners?',
      status: 'proposed',
      origin: 'generated',
      topic_id: '55555555-5555-4555-8555-555555555555',
    });
    mswServer.use(
      http.post(`/api/v1/prompt-sets/${SET_ID}/generate`, () =>
        HttpResponse.json(
          {
            generated: [generated],
            topics: [
              makeTopic({ id: '55555555-5555-4555-8555-555555555555', proposed_count: 1 }),
              makeTopic({
                id: '77777777-7777-4777-8777-777777777777',
                name: 'Apparel',
              }),
            ],
            dropped_duplicates: 1,
          },
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

    // Derived from unique non-null topic_id values on `generated` (1), not the
    // two touched topics; the dropped duplicate is still reported.
    const summary = await within(dialog).findByText(/across 1 topic/);
    expect(summary).toHaveTextContent(/1 duplicate skipped/);
    expect(within(dialog).queryByText(/across 2 topics/)).not.toBeInTheDocument();
  });

  it('resets the topic filter to All topics so new rows are visible after generating', async () => {
    const user = userEvent.setup();
    const viewedTopic = makeTopic({ id: '55555555-5555-4555-8555-555555555555', active_count: 1 });
    // A generated row lands in a different topic than the one being viewed.
    const otherTopicId = '77777777-7777-4777-8777-777777777777';
    const generated = makePrompt({
      id: '66666666-6666-4666-8666-666666666666',
      text: 'Generated elsewhere prompt',
      status: 'proposed',
      origin: 'generated',
      topic_id: otherTopicId,
    });
    baseHandlers(
      [makePrompt({ topic_id: viewedTopic.id, text: 'Topic-scoped prompt' })],
      [viewedTopic, makeTopic({ id: otherTopicId, name: 'Apparel' })],
    );
    // After generation the prompt-set refetch must include the new proposed
    // row so it can render under the reset (All topics) Proposed tab.
    let generatedYet = false;
    mswServer.use(
      http.get('/api/v1/prompt-sets', () =>
        HttpResponse.json([
          makeSet(
            generatedYet
              ? [makePrompt({ topic_id: viewedTopic.id, text: 'Topic-scoped prompt' }), generated]
              : [makePrompt({ topic_id: viewedTopic.id, text: 'Topic-scoped prompt' })],
          ),
        ]),
      ),
      http.post(`/api/v1/prompt-sets/${SET_ID}/generate`, () => {
        generatedYet = true;
        return HttpResponse.json(
          {
            generated: [generated],
            topics: [makeTopic({ id: otherTopicId, name: 'Apparel', proposed_count: 1 })],
            dropped_duplicates: 0,
          },
          { status: 201 },
        );
      }),
    );

    renderPage();
    await screen.findByText('Topic-scoped prompt', undefined, { timeout: 5000 });

    // Narrow to the viewed topic first.
    await user.click(await screen.findByRole('button', { name: /^Footwear/ }));

    // Generate — the run lands the row in a different topic (Apparel).
    await user.click(screen.getByRole('button', { name: /Generate prompts & topics/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(
      within(dialog).getByRole('checkbox', { name: /Confirm sending brand details/i }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Generate' }));
    await within(dialog).findByText(/1 prompt proposed for review/);
    await user.click(within(dialog).getByRole('button', { name: 'Close' }));

    // Topic filter reset to All topics + Proposed tab selected → the new row
    // is visible even though it landed in a topic the user was not viewing.
    expect(screen.getByRole('tab', { name: /Proposed/ })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('Generated elsewhere prompt')).toBeInTheDocument();
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
    baseHandlers([
      makePrompt(),
      makePrompt({
        id: '44444444-4444-4444-8444-444444444444',
        text: 'Nike vs Adidas',
        intent: 'comparison',
      }),
    ]);
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
